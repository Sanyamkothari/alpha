"""What the machine has cost, in money and in time.

Two scarce resources, and they are not the same shape:

* **LLM credit** — real money, read from ``llm_runs.cost_usd``. OpenRouter
  reports the billed figure per call, so this is what was actually charged, not
  an estimate off a price table.
* **BRAIN simulation slots** — no money changes hands, but the account is capped
  at 3 concurrent and a simulation takes ~90 s, so throughput is hard-limited to
  roughly 2,880/day. Time is the real budget, and it is the one that constrains
  how many ideas can be tested. It is reported as such rather than quietly
  ignored because it is free.

The headline is **cost per submittable alpha** (STRATEGY.md §9). Total spend on
its own says nothing; spend divided by output is the number that tells you
whether the machine is worth running.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.prompts import LLMRun
from app.models.results import AlphaMetric

# Measured on this account: 3 concurrent slots, ~90 s per simulation.
SECONDS_PER_SIM = 90.0
MAX_CONCURRENT = 3
DAILY_SIM_CAPACITY = int(86_400 / SECONDS_PER_SIM * MAX_CONCURRENT)


@dataclass
class TaskSpend:
    task: str
    model_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _since(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def llm_spend(db: Session) -> dict:
    rows = db.execute(
        select(
            LLMRun.task,
            LLMRun.model_id,
            func.count(LLMRun.id),
            func.coalesce(func.sum(LLMRun.input_tokens), 0),
            func.coalesce(func.sum(LLMRun.output_tokens), 0),
            func.coalesce(func.sum(LLMRun.cost_usd), 0.0),
        ).group_by(LLMRun.task, LLMRun.model_id)
    ).all()

    by_task = [
        TaskSpend(
            task=t or "—",
            model_id=m or "—",
            calls=c,
            input_tokens=i,
            output_tokens=o,
            cost_usd=float(cost),
        )
        for t, m, c, i, o, cost in rows
    ]
    by_task.sort(key=lambda r: r.cost_usd, reverse=True)

    total = float(db.scalar(select(func.coalesce(func.sum(LLMRun.cost_usd), 0.0))) or 0.0)
    day = float(
        db.scalar(
            select(func.coalesce(func.sum(LLMRun.cost_usd), 0.0)).where(
                LLMRun.created_at >= _since(24)
            )
        )
        or 0.0
    )
    calls = int(db.scalar(select(func.count(LLMRun.id))) or 0)
    tok_in = int(db.scalar(select(func.coalesce(func.sum(LLMRun.input_tokens), 0))) or 0)
    tok_out = int(db.scalar(select(func.coalesce(func.sum(LLMRun.output_tokens), 0))) or 0)

    return {
        "total_usd": round(total, 6),
        "last_24h_usd": round(day, 6),
        "calls": calls,
        "input_tokens": tok_in,
        "output_tokens": tok_out,
        "by_task": [
            {
                "task": r.task,
                "model_id": r.model_id,
                "calls": r.calls,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": round(r.cost_usd, 6),
            }
            for r in by_task
        ],
    }


def measured_sim_seconds(db: Session, *, sample: int = 200) -> tuple[float | None, int]:
    """Median seconds per simulation, measured. Returns (median, sample_size).

    Every time estimate used to rest on one hardcoded 90 s constant taken from
    watching a single run. BRAIN's speed varies with platform load and with how
    heavy the expression is, so the number is now read back from what actually
    happened. Median rather than mean: one simulation that stalled behind the
    concurrency cap should not drag the estimate for all the others.

    Falls back to (None, 0) until enough runs carry a timing, so callers keep the
    prior instead of trusting a number derived from two samples.
    """
    rows = (
        db.execute(
            select(AlphaMetric.extra)
            .where(AlphaMetric.extra.is_not(None))
            .order_by(AlphaMetric.id.desc())
            .limit(sample)
        )
        .scalars()
        .all()
    )
    seconds = []
    for extra in rows:
        if not isinstance(extra, dict):
            continue
        v = extra.get("sim_seconds")
        if isinstance(v, (int, float)) and 0 < v < 3600:
            seconds.append(float(v))
    if len(seconds) < 3:
        return None, len(seconds)
    seconds.sort()
    mid = len(seconds) // 2
    med = seconds[mid] if len(seconds) % 2 else (seconds[mid - 1] + seconds[mid]) / 2
    return round(med, 1), len(seconds)


def simulation_spend(db: Session) -> dict:
    """BRAIN throughput used. The currency here is wall-clock, not money."""
    total = int(db.scalar(select(func.count(AlphaMetric.id))) or 0)
    day = int(
        db.scalar(select(func.count(AlphaMetric.id)).where(AlphaMetric.created_at >= _since(24)))
        or 0
    )
    measured, n = measured_sim_seconds(db)
    per_sim = measured or SECONDS_PER_SIM
    # Slots run 3-wide, so N simulations occupy N/3 slot-hours of wall clock.
    hours = round(total * per_sim / MAX_CONCURRENT / 3600, 1)
    capacity = int(86_400 / per_sim * MAX_CONCURRENT)
    return {
        "simulations": total,
        "last_24h": day,
        "wall_clock_hours": hours,
        "seconds_per_sim": per_sim,
        "seconds_per_sim_measured": measured is not None,
        "timing_sample": n,
        "sims_per_hour": round(3600 / per_sim * MAX_CONCURRENT, 1),
        "daily_capacity": capacity,
        "capacity_used_pct": round(100 * day / capacity, 1) if capacity else 0,
        "concurrency_cap": MAX_CONCURRENT,
    }


def summary(db: Session) -> dict:
    llm = llm_spend(db)
    sims = simulation_spend(db)

    passing = int(
        db.scalar(select(func.count(AlphaMetric.id)).where(AlphaMetric.passed_all_checks.is_(True)))
        or 0
    )
    submitted = int(
        db.scalar(select(func.count(Alpha.id)).where(Alpha.status == AlphaStatus.SUBMITTED.value))
        or 0
    )

    # The headline. Divide by submitted where the operator has recorded any,
    # otherwise by alphas that cleared every check — and say which denominator
    # was used, because the two numbers mean different things.
    denom, basis = (submitted, "submitted") if submitted else (passing, "passing")
    per_alpha = round(llm["total_usd"] / denom, 6) if denom else None

    return {
        "llm": llm,
        "simulations": sims,
        "output": {"passing": passing, "submitted": submitted},
        "cost_per_alpha_usd": per_alpha,
        "cost_per_alpha_basis": basis,
        "note": (
            "LLM cost is the billed figure reported by the provider, not an estimate. "
            "BRAIN simulations cost no money — the scarce resource there is the "
            f"{MAX_CONCURRENT}-concurrent cap, about {DAILY_SIM_CAPACITY:,} runs/day."
        ),
    }
