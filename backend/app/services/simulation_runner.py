"""Stage 2 — run untested alphas on BRAIN and import their results.

Throughput is the point (STRATEGY.md Rule 4). BRAIN caps an account at **3**
concurrent simulations — a 4th returns ``429
CONCURRENT_SIMULATION_LIMIT_EXCEEDED`` — so the ceiling is roughly
``3 / 90s ≈ 2/min ≈ 2,800/day``. That is well above the 200–500/day target, and
the cap is not something to work around: it is the platform telling you its
budget.

Threading model: **worker threads do HTTP only, the main thread does every
database write.** SQLAlchemy sessions are not thread-safe, and the failure mode
of sharing one across a pool is intermittent and awful to debug. Keeping writes
single-threaded costs nothing here — the simulations, not the inserts, are the
bottleneck by three orders of magnitude.
"""

from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, ImportSource
from app.services.alpha_library import transition_status
from app.services.brain import BrainClient, SimulationSettings
from app.services.brain.client import normalize_is_block
from app.services.result_import import import_result

log = structlog.get_logger("simulation_runner")


@dataclass
class BatchResult:
    simulated: int = 0
    failed: int = 0
    passed_all_checks: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "simulated": self.simulated,
            "failed": self.failed,
            "passed_all_checks": self.passed_all_checks,
            "errors": self.errors[:10],
        }


def _settings_for(alpha: Alpha) -> SimulationSettings:
    """Build the request settings from the alpha's own stored block.

    The alpha row is the source of truth: a family sweeps neutralization/decay/
    truncation, so re-deriving settings from defaults here would silently
    collapse every variant onto the same backtest.
    """
    return SimulationSettings(
        region=alpha.region,
        universe=alpha.universe,
        delay=alpha.delay,
        decay=alpha.decay if alpha.decay is not None else 0,
        neutralization=alpha.neutralization or "SUBINDUSTRY",
        truncation=alpha.truncation if alpha.truncation is not None else 0.08,
    )


def pending_alpha_ids(limit: int = 100, *, family_key: str | None = None) -> list[int]:
    """Ids of validator-passing alphas that have never been simulated."""
    with session_scope() as db:
        q = (
            select(Alpha.id)
            .where(Alpha.status == AlphaStatus.UNTESTED.value, Alpha.is_valid.is_(True))
            .order_by(Alpha.id)
            .limit(limit)
        )
        if family_key:
            q = q.where(Alpha.family_key == family_key)
        return list(db.execute(q).scalars().all())


def run_batch(alpha_ids: list[int], *, max_concurrent: int = 3) -> BatchResult:
    """Simulate the given alphas and import every result."""
    out = BatchResult()
    if not alpha_ids:
        return out

    with session_scope() as db:
        jobs = [
            (a.id, a.expression, _settings_for(a))
            for a in db.execute(select(Alpha).where(Alpha.id.in_(alpha_ids))).scalars().all()
        ]

    with BrainClient(max_concurrent=max_concurrent) as brain:

        def work(job: tuple[int, str, SimulationSettings]) -> tuple[int, dict | None, str | None]:
            aid, expression, sim_settings = job
            timing: dict = {}
            try:
                remote_id = brain.simulate(expression, sim_settings, timing=timing)
                payload = brain.alpha(remote_id)
                # The platform's own turnaround, measured inside the concurrency
                # semaphore. Timing from out here would include the wait for a
                # free slot, which the wave-based ETA already models — the first
                # attempt at this recorded 231s for jobs that were simply queued.
                if timing.get("seconds") is not None:
                    payload["_sim_seconds"] = timing["seconds"]
                return aid, payload, None
            except Exception as exc:  # noqa: BLE001 - one bad alpha must not kill the batch
                return aid, None, f"{type(exc).__name__}: {exc}"

        with cf.ThreadPoolExecutor(max_workers=max_concurrent) as pool:
            for aid, payload, err in pool.map(work, jobs):
                if err or not payload:
                    out.failed += 1
                    out.errors.append(f"alpha {aid}: {err}")
                    log.warning("simulation_failed", alpha_id=aid, error=err)
                    continue

                is_block = dict(payload.get("is") or {})
                if payload.get("id"):
                    is_block["id"] = payload["id"]
                if payload.get("_sim_seconds") is not None:
                    # Rides in the metrics `extra` JSON — no migration needed.
                    is_block["sim_seconds"] = payload["_sim_seconds"]
                if not is_block:
                    out.failed += 1
                    out.errors.append(f"alpha {aid}: no `is` block returned")
                    continue

                # Every DB write happens here, on the main thread.
                with session_scope() as db:
                    alpha = db.get(Alpha, aid)
                    if alpha is None:
                        continue
                    res = import_result(
                        db,
                        alpha,
                        normalize_is_block(is_block),
                        source=ImportSource.BRAIN_API.value,
                    )
                    passed = res.metrics.passed_all_checks
                    # `passed_all_checks` is deliberately TRI-state: None means
                    # BRAIN returned no scorable checks (every one PENDING, or the
                    # block was malformed). Collapsing None to REJECTED throws away
                    # a real simulation and the result_import module goes to some
                    # trouble to keep the distinction.
                    if passed is True:
                        new_status = AlphaStatus.PASSED.value
                    elif passed is False:
                        new_status = AlphaStatus.REJECTED.value
                    else:
                        new_status = AlphaStatus.TESTING.value  # ran, not yet scorable
                    transition_status(db, alpha, new_status, note=f"brain sim {payload.get('id')}")
                    if passed:
                        out.passed_all_checks += 1
                out.simulated += 1
                log.info("simulated", alpha_id=aid, remote=payload.get("id"))

    log.info("batch_complete", **out.as_dict())
    return out
