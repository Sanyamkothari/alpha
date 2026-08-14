"""In-process job registry for long-running work launched from the UI.

Expanding and simulating a family takes minutes, so the HTTP request that starts
it cannot wait. This is the smallest thing that solves that honestly.

**Why not Celery/Redis/arq.** One local user, one process, jobs measured in
minutes. A broker would add a daemon to run, a queue to monitor and a
serialisation boundary to debug, in exchange for durability this tool does not
need. The cost of the simple choice is stated rather than hidden: **jobs die if
the server restarts**, and the UI is told so via ``lost`` rather than leaving a
job spinning at "running" forever.

Progress is written by the worker under a lock and read by polling. There is no
websocket: a 1-second poll of a dict is not a bottleneck at this scale, and it
cannot get into the desynchronised-stream states a socket can.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from app.config import USER_DATA_DIR

log = structlog.get_logger("jobs")

# Finished jobs are kept so the UI can still show the outcome after a reload.
# Bounded so a long session cannot grow the dict without limit.
MAX_RETAINED_JOBS = 50


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = "queued"  # queued | running | done | failed | cancelled | interrupted
    total: int = 0
    completed: int = 0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    finished_at: str | None = None
    started_mono: float | None = None  # monotonic clock — for elapsed/ETA only

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("started_mono", None)
        d["percent"] = round(100 * self.completed / self.total) if self.total else 0
        d["elapsed_s"] = self.elapsed_s
        d["eta_s"] = self.eta_s
        d["eta_text"] = fmt_duration(self.eta_s) if self.eta_s is not None else None
        return d

    @property
    def elapsed_s(self) -> float | None:
        if self.started_mono is None:
            return None
        return round(time.monotonic() - self.started_mono, 1)

    @property
    def eta_s(self) -> float | None:
        """Seconds remaining, measured where possible and assumed where not."""
        if self.status in ("done", "failed", "cancelled", "interrupted") or not self.total:
            return None
        remaining = max(0, self.total - self.completed)
        if not remaining:
            return 0.0
        elapsed = self.elapsed_s
        if elapsed and self.completed >= 1:
            waves_done = max(1, math.ceil(self.completed / MAX_CONCURRENT))
            waves_left = math.ceil(remaining / MAX_CONCURRENT)
            return round(waves_left * (elapsed / waves_done), 0)
        return round(math.ceil(remaining / MAX_CONCURRENT) * _prior_seconds_per_sim(), 0)


# Priors for the pre-first-completion estimate. Measured on this account:
# one simulation takes ~90 s and BRAIN caps the account at 3 in flight.
SECONDS_PER_SIM = 90.0
MAX_CONCURRENT = 3


def _prior_seconds_per_sim() -> float:
    """Seconds per simulation for a job that has not completed anything yet."""
    try:
        from app.db import session_scope
        from app.services.spend import measured_sim_seconds

        with session_scope() as db:
            measured, _ = measured_sim_seconds(db)
        return measured or SECONDS_PER_SIM
    except Exception:  # noqa: BLE001 - cosmetic estimate, never fatal
        return SECONDS_PER_SIM


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


class JobRegistry:
    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or (
            USER_DATA_DIR / "database" / "jobs.json"
            if (USER_DATA_DIR / "database").exists()
            else USER_DATA_DIR / "jobs.json"
        )
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._load_and_recover()

    def _load_and_recover(self) -> None:
        with self._lock:
            if not self._storage_path.exists():
                return
            try:
                data = json.loads(self._storage_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict) or "id" not in item:
                            continue
                        job = Job(
                            id=item["id"],
                            kind=item.get("kind", "job"),
                            label=item.get("label", "Job"),
                            status=item.get("status", "done"),
                            total=item.get("total", 0),
                            completed=item.get("completed", 0),
                            message=item.get("message", ""),
                            result=item.get("result"),
                            error=item.get("error"),
                            created_at=item.get("created_at", ""),
                            finished_at=item.get("finished_at"),
                        )
                        # Jobs that were active when the process terminated are recovered as interrupted
                        if job.status in ("queued", "running"):
                            job.status = "interrupted"
                            job.error = "interrupted on server restart"
                            if not job.finished_at:
                                job.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
                        self._jobs[job.id] = job
                self._evict_locked()
                self._save_locked()
            except Exception as exc:
                log.warning("job_storage_load_failed", error=str(exc))

    def _save_locked(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = []
            for j in sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)[
                :MAX_RETAINED_JOBS
            ]:
                d = asdict(j)
                d.pop("started_mono", None)
                payload.append(d)
            self._storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("job_storage_save_failed", error=str(exc))

    # ---- worker-facing ----

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            self._save_locked()

    def progress(self, job_id: str, *, completed: int | None = None, message: str = "") -> None:
        """Called from inside a running job to report progress."""
        fields: dict[str, Any] = {}
        if completed is not None:
            fields["completed"] = completed
        if message:
            fields["message"] = message
        if fields:
            self._update(job_id, **fields)

    # ---- caller-facing ----

    def launch(
        self,
        kind: str,
        label: str,
        fn: Callable[..., dict[str, Any]],
        *,
        total: int = 0,
        **kwargs: Any,
    ) -> Job:
        """Start ``fn`` on a background thread. ``fn`` receives ``job_id``."""
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            label=label,
            total=total,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._evict_locked()
            self._save_locked()

        def runner() -> None:
            self._update(job.id, status="running", started_mono=time.monotonic())
            try:
                result = fn(job_id=job.id, **kwargs)
                self._update(
                    job.id,
                    status="done",
                    result=result,
                    finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
                )
                log.info("job_done", job=job.id, kind=kind)
            except Exception as exc:  # noqa: BLE001 - surface it, never crash the thread
                self._update(
                    job.id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
                )
                log.warning("job_failed", job=job.id, kind=kind, error=str(exc))
                log.debug("job_traceback", tb=traceback.format_exc())

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def active(self) -> list[Job]:
        return [j for j in self.list() if j.status in ("queued", "running")]

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs once past the retention cap."""
        finished = [
            j
            for j in self._jobs.values()
            if j.status in ("done", "failed", "cancelled", "interrupted")
        ]
        if len(finished) <= MAX_RETAINED_JOBS:
            return
        finished.sort(key=lambda j: j.created_at)
        for job in finished[: len(finished) - MAX_RETAINED_JOBS]:
            self._jobs.pop(job.id, None)


_registry = JobRegistry()


def registry() -> JobRegistry:
    return _registry


@dataclass
class RunFamilyParams:
    field_code: str
    denominator: str | None = None
    mechanism: str = ""
    max_candidates: int = 384
    simulate: int = 24
    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    backfill_days: int | None = 120
    extra: dict = field(default_factory=dict)


def run_family_job(*, job_id: str, params: RunFamilyParams) -> dict[str, Any]:
    """Expand a family, save it, and simulate a capped subset.

    Mirrors ``scripts.run_family`` so the UI and the CLI cannot drift into doing
    different things — including the preflight, which stops a few hundred dead
    alpha rows being written for a config the account cannot simulate.
    """
    from app.db import session_scope
    from app.services.alpha_library import AlphaSettings, create_alpha
    from app.services.brain import BrainClient, SimulationSettings
    from app.services.constructor import FamilySpec, expand
    from app.services.simulation_runner import pending_alpha_ids, run_batch

    reg = registry()
    settings = AlphaSettings(region=params.region, universe=params.universe, delay=params.delay)
    spec = FamilySpec(
        field_code=params.field_code,
        denominator=params.denominator,
        mechanism=params.mechanism or f"{params.field_code} signal",
        backfill_days=params.backfill_days,
    )
    family_key = spec.family_key(settings)

    if params.simulate:
        reg.progress(job_id, message="preflight: is this config simulatable?")
        with BrainClient() as brain:
            ok, why = brain.config_available(
                SimulationSettings(
                    region=params.region, universe=params.universe, delay=params.delay
                )
            )
        if not ok:
            raise RuntimeError(
                f"{params.region}/{params.universe}/delay{params.delay} is not "
                f"simulatable on this account: {why}"
            )

    reg.progress(job_id, message="expanding the grid")
    with session_scope() as db:
        candidates = expand(db, spec, base_settings=settings, max_candidates=params.max_candidates)
    if not candidates:
        raise RuntimeError(
            f"nothing emitted for {params.field_code!r} — does the field exist "
            f"for {params.region}/{params.universe}/delay{params.delay}?"
        )

    reg.progress(job_id, message=f"saving {len(candidates)} candidates")
    created = duplicate = 0
    with session_scope() as db:
        for cand in candidates:
            try:
                res = create_alpha(
                    db,
                    cand.expression,
                    cand.settings,
                    family_key=cand.family_key,
                    grid=cand.grid,
                    source="constructor",
                )
            except Exception:  # noqa: BLE001 - validator gate; count and continue
                continue
            if not res.created:
                duplicate += 1
                continue
            created += 1

    simulated = {"simulated": 0, "failed": 0, "passed_all_checks": 0}
    if params.simulate:
        ids = pending_alpha_ids(limit=params.simulate, family_key=family_key)
        reg.progress(job_id, message=f"simulating {len(ids)} on BRAIN (3 concurrent)")
        with registry_progress(job_id, len(ids)):
            batch = run_batch(ids)
        simulated = {
            "simulated": batch.simulated,
            "failed": batch.failed,
            "passed_all_checks": batch.passed_all_checks,
        }

    return {
        "family_key": family_key,
        "expanded": len(candidates),
        "created": created,
        "duplicate": duplicate,
        **simulated,
    }


class registry_progress:  # noqa: N801 - used as a context manager, reads better lowercase
    """Poll the database while a batch runs so the UI has live progress.

    ``run_batch`` writes each result as it lands but has no progress callback.
    Rather than thread one through and complicate its signature, this samples the
    row count on a timer — the numbers the UI shows are then the same numbers
    that are actually committed, which is the honest thing to display.
    """

    def __init__(self, job_id: str, total: int, interval: float = 3.0) -> None:
        self.job_id = job_id
        self.total = total
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> registry_progress:
        from sqlalchemy import func, select

        from app.db import session_scope
        from app.models.results import AlphaMetric

        reg = registry()
        reg.progress(self.job_id, completed=0)

        with session_scope() as db:
            baseline = db.scalar(select(func.count(AlphaMetric.id))) or 0

        def poll() -> None:
            while not self._stop.wait(self.interval):
                try:
                    with session_scope() as db:
                        now = db.scalar(select(func.count(AlphaMetric.id))) or 0
                    reg.progress(self.job_id, completed=min(self.total, now - baseline))
                except Exception:  # noqa: BLE001 - progress is cosmetic, never fatal
                    pass

        self._thread = threading.Thread(target=poll, name=f"progress-{self.job_id}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        registry().progress(self.job_id, completed=self.total)
