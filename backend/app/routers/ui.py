"""JSON endpoints backing the single-page UI.

Thin by design: every number here comes from a service that already computes it
(``plateau.evaluate``, ``allocator.suggest``, ``simulation_runner``). If a value
needs deriving, it belongs in the service so the CLI and the UI cannot disagree
about what "promoted" means.

Note ``family_key`` is passed as a **query parameter**, never a path segment — it
contains slashes (``liabilities/cap@USA/TOP3000/d1``) and would otherwise be
mangled by path routing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric
from app.services.allocator import dataset_stats, suggest
from app.services.alpha_library import transition_status
from app.services.jobs import RunFamilyParams, registry, run_family_job
from app.services.plateau import DECAY_LADDER, WINDOW_LADDER, evaluate, load_surface
from app.services.spend import summary as spend_summary

router = APIRouter(prefix="/ui", tags=["ui"])


def _families(db: Session) -> list[str]:
    return [
        k
        for (k,) in db.execute(
            select(Alpha.family_key).where(Alpha.family_key.is_not(None)).group_by(Alpha.family_key)
        ).all()
    ]


def _verdict_row(v: Any, db: Session) -> dict[str, Any]:
    alpha = db.get(Alpha, v.alpha_id)
    return {
        "alpha_id": v.alpha_id,
        "expression": v.expression,
        "sharpe": v.sharpe,
        "fitness": v.fitness,
        "neighbour_median_sharpe": v.neighbour_median_sharpe,
        "plateau_ratio": v.plateau_ratio,
        "is_plateau": v.is_plateau,
        "clears_bar": v.clears_bar,
        "neighbours_simulated": v.neighbours_simulated,
        "neighbours_possible": v.neighbours_possible,
        "family_size": v.family_size,
        "haircut_bar": v.haircut_bar,
        "is_correlated": getattr(v, "is_correlated", False),
        "correlation_collision": getattr(v, "correlation_collision", None),
        "promoted": v.promoted,
        "dsr": getattr(v, "dsr", None),
        "dsr_passed": getattr(v, "dsr_passed", None),
        "gate_mode": getattr(v, "gate_mode", "COLD_START_FALLBACK"),
        "subperiod_passed": getattr(v, "subperiod_passed", None),
        "reasons": v.reasons,
        "status": alpha.status if alpha else None,
        "settings": (
            {
                "region": alpha.region,
                "universe": alpha.universe,
                "delay": alpha.delay,
                "neutralization": alpha.neutralization,
                "decay": alpha.decay,
                "truncation": alpha.truncation,
            }
            if alpha
            else None
        ),
    }


@router.get("/summary")
def summary(db: Session = Depends(get_db)) -> dict:
    """Everything the landing view needs, in one round trip."""
    total = db.scalar(select(func.count(Alpha.id))) or 0
    simulated = db.scalar(select(func.count(AlphaMetric.id))) or 0
    passing = (
        db.scalar(select(func.count(AlphaMetric.id)).where(AlphaMetric.passed_all_checks.is_(True)))
        or 0
    )
    submitted = (
        db.scalar(select(func.count(Alpha.id)).where(Alpha.status == AlphaStatus.SUBMITTED.value))
        or 0
    )

    # An alpha the operator has already acted on must leave the queue. Without
    # this the morning list never empties: yesterday's submitted alpha is still
    # at the top today, and the obvious next action on it is to submit it again.
    # They remain visible under Submitted — removed from the queue, not hidden.
    ACTIONED = {AlphaStatus.SUBMITTED.value, AlphaStatus.ARCHIVED.value}

    promoted: list[dict] = []
    near: list[dict] = []
    for family in _families(db):
        for v in evaluate(db, family):
            if not (v.promoted or v.clears_bar):
                continue
            row = _verdict_row(v, db)
            if row.get("status") in ACTIONED:
                continue
            (promoted if v.promoted else near).append(row)

    promoted.sort(key=lambda r: r["sharpe"] or 0, reverse=True)
    near.sort(key=lambda r: r["sharpe"] or 0, reverse=True)

    return {
        "counts": {
            "alphas": total,
            "simulated": simulated,
            "passing": passing,
            "submitted": submitted,
            "families": len(_families(db)),
        },
        # Counts BEFORE the display cap. Returning len(promoted[:25]) as the
        # "promoted" number turns a 40-survivor morning into "25 survived".
        "shortlist_total": len(promoted),
        "near_misses_total": len(near),
        "shortlist": promoted[:25],
        "near_misses": near[:15],
        "jobs": [j.as_dict() for j in registry().list()[:8]],
    }


@router.get("/families")
def families(db: Session = Depends(get_db)) -> dict:
    out = []
    for key in _families(db):
        points = load_surface(db, key)
        done = [p for p in points if p.sharpe is not None]
        total = db.scalar(select(func.count(Alpha.id)).where(Alpha.family_key == key)) or 0
        verdicts = evaluate(db, key) if done else []
        out.append(
            {
                "family_key": key,
                "alphas": total,
                "simulated": len(done),
                "promoted": sum(1 for v in verdicts if v.promoted),
                "best_sharpe": max((p.sharpe or 0) for p in done) if done else None,
                "structures": len({p.structure for p in done}),
            }
        )
    out.sort(key=lambda r: (r["promoted"], r["best_sharpe"] or 0), reverse=True)
    return {"families": out}


@router.get("/surfaces")
def surfaces(
    family: str = Query(..., description="family_key; contains slashes, hence a query param"),
    db: Session = Depends(get_db),
) -> dict:
    """Every (window x decay) surface in a family, ready to render as heatmaps.

    Includes emitted-but-unsimulated cells (``sharpe: null``) so the grid can
    draw a hole rather than silently shrinking — and so the UI can offer to
    simulate exactly the missing neighbours of a candidate the filter could not
    judge.
    """
    points = load_surface(db, family, include_unsimulated=True)
    if not points:
        return {
            "family_key": family,
            "windows": WINDOW_LADDER,
            "decays": DECAY_LADDER,
            "surfaces": [],
        }

    promoted_ids = {v.alpha_id for v in evaluate(db, family) if v.promoted}

    by_structure: dict[tuple, list] = {}
    for p in points:
        by_structure.setdefault(p.structure, []).append(p)

    out = []
    for structure, pts in by_structure.items():
        ts, cs, group, neutralization, truncation = structure
        cells = {
            f"{p.window}:{p.decay}": {
                "alpha_id": p.alpha_id,
                "sharpe": p.sharpe,
                "fitness": p.fitness,
                "turnover": p.turnover,
                "simulated": p.sharpe is not None,
                "passed": bool(p.passed_all_checks),
                "promoted": p.alpha_id in promoted_ids,
                "expression": p.expression,
            }
            for p in pts
        }
        out.append(
            {
                "label": {
                    "ts": ts,
                    "cs": cs,
                    "group": group,
                    "neutralization": neutralization,
                    "truncation": truncation,
                },
                "best_sharpe": max((p.sharpe or 0) for p in pts),
                "simulated": sum(1 for p in pts if p.sharpe is not None),
                "filled": len(pts),
                "cells": cells,
            }
        )
    out.sort(key=lambda s: s["best_sharpe"], reverse=True)
    return {
        "family_key": family,
        "windows": list(WINDOW_LADDER),
        "decays": list(DECAY_LADDER),
        "surfaces": out,
    }


@router.post("/fill")
def fill_cells(payload: dict = Body(...)) -> dict:
    """Simulate a specific set of already-emitted alphas.

    The recovery path out of "no simulated neighbours — surface incomplete".
    The candidates already exist (the constructor emits ~384 but only a subset
    is simulated), so filling four holes is four simulations, not a new family.
    """
    ids = payload.get("alpha_ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=422, detail="alpha_ids must be a list of ints")
    if not ids:
        raise HTTPException(status_code=422, detail="alpha_ids is empty")
    ids = ids[:24]

    from app.services.jobs import registry_progress
    from app.services.simulation_runner import run_batch

    def work(*, job_id: str) -> dict:
        # Without the progress poller `completed` never leaves 0, so the bar sits
        # at 0% for the whole run and the ETA can never correct itself from the
        # prior to a measured rate.
        with registry_progress(job_id, len(ids)):
            return run_batch(ids).as_dict()

    job = registry().launch("fill", f"fill {len(ids)} cells", work, total=len(ids))
    return job.as_dict()


@router.get("/spend")
def spend(db: Session = Depends(get_db)) -> dict:
    """Money spent on LLM credit, and BRAIN throughput consumed."""
    return spend_summary(db)


@router.get("/library")
def library(
    q: str = "",
    status: str = "",
    simulated: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> dict:
    """Every alpha, searchable. Backs the clickable counters in the top bar.

    Those counters read as navigation because they are — an operator who sees
    "1,009 alphas" wants to click it. Without a destination the number is a
    dead end, so this is the destination.
    """
    stmt = (
        select(Alpha, AlphaMetric)
        .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .order_by(AlphaMetric.sharpe.desc().nulls_last(), Alpha.id.desc())
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Alpha.expression.ilike(like) | Alpha.family_key.ilike(like))
    if status:
        stmt = stmt.where(Alpha.status == status)
    if simulated:
        stmt = stmt.where(AlphaMetric.id.is_not(None))

    total = db.scalar(select(func.count(Alpha.id))) or 0
    # How many the filter actually matched, distinct from how many we return.
    # Reporting only "shown / total" cannot tell "matched everything" apart from
    # "matched nothing and you are seeing the cap" — the two look identical.
    count_stmt = (
        select(func.count(func.distinct(Alpha.id)))
        .select_from(Alpha)
        .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
    )
    if q:
        count_stmt = count_stmt.where(
            Alpha.expression.ilike(f"%{q}%") | Alpha.family_key.ilike(f"%{q}%")
        )
    if status:
        count_stmt = count_stmt.where(Alpha.status == status)
    if simulated:
        count_stmt = count_stmt.where(AlphaMetric.id.is_not(None))
    matched = db.scalar(count_stmt) or 0

    seen: set[int] = set()
    rows = []
    for alpha, metric in db.execute(stmt.limit(limit * 2)).all():
        if alpha.id in seen:
            continue
        seen.add(alpha.id)
        rows.append(
            {
                "alpha_id": alpha.id,
                "expression": alpha.expression,
                "family_key": alpha.family_key,
                "status": alpha.status,
                "sharpe": metric.sharpe if metric else None,
                "fitness": metric.fitness if metric else None,
                "turnover": metric.turnover if metric else None,
                "passed": bool(metric.passed_all_checks) if metric else False,
            }
        )
        if len(rows) >= limit:
            break
    return {"alphas": rows, "total": total, "matched": matched, "shown": len(rows)}


@router.get("/portfolio")
def portfolio(db: Session = Depends(get_db)) -> dict:
    """Alphas the operator submitted, plus ones still cleared and waiting.

    Pressing `s` used to make a row disappear with nowhere to review it. This is
    the ledger of what actually went out — and the list you need in front of you
    when thinking about correlation, since BRAIN pays only for uncorrelated
    alphas and every submission makes the next one harder.
    """
    rows = db.execute(
        select(Alpha, AlphaMetric)
        .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(Alpha.status.in_([AlphaStatus.SUBMITTED.value, AlphaStatus.PASSED.value]))
        .order_by(Alpha.updated_at.desc())
    ).all()
    seen: set[int] = set()
    out = []
    for alpha, metric in rows:
        if alpha.id in seen:
            continue
        seen.add(alpha.id)
        out.append(
            {
                "alpha_id": alpha.id,
                "expression": alpha.expression,
                "family_key": alpha.family_key,
                "status": alpha.status,
                "sharpe": metric.sharpe if metric else None,
                "fitness": metric.fitness if metric else None,
                "turnover": metric.turnover if metric else None,
                "neutralization": alpha.neutralization,
                "decay": alpha.decay,
                "updated_at": str(alpha.updated_at),
            }
        )
    return {"portfolio": out}


@router.get("/datasets")
def datasets(
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    db: Session = Depends(get_db),
) -> dict:
    stats = dataset_stats(db, region=region, delay=delay, universe=universe)
    return {
        "datasets": [
            {
                "dataset_code": s.dataset_code,
                "name": s.name,
                "field_count": s.field_count,
                "avg_user_count": round(s.avg_user_count),
                "crowding_score": round(s.crowding_score, 3),
                "tried": s.tried,
                "passed": s.passed,
                "hit_rate": s.hit_rate,
            }
            for s in sorted(stats, key=lambda s: s.avg_user_count)
        ]
    }


@router.get("/suggestions")
def suggestions(
    n: int = 6,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    db: Session = Depends(get_db),
) -> dict:
    return {
        "suggestions": [
            {
                "field_code": s.field_code,
                "dataset_code": s.dataset_code,
                "denominator": s.denominator,
                "reason": s.reason,
                "user_count": s.user_count,
                "coverage": s.coverage,
            }
            for s in suggest(db, region=region, delay=delay, universe=universe, n=n)
        ]
    }


@router.post("/runs")
def launch_run(payload: dict = Body(...)) -> dict:
    """Start a family run in the background. Returns immediately with a job id."""
    field_code = (payload.get("field_code") or "").strip()
    if not field_code:
        raise HTTPException(status_code=422, detail="field_code is required")

    params = RunFamilyParams(
        field_code=field_code,
        denominator=(payload.get("denominator") or None),
        mechanism=payload.get("mechanism") or "",
        max_candidates=int(payload.get("max_candidates") or 384),
        simulate=int(payload.get("simulate") or 24),
        region=payload.get("region") or "USA",
        universe=payload.get("universe") or "TOP3000",
        delay=int(payload.get("delay") or 1),
    )
    label = f"{params.field_code}" + (f"/{params.denominator}" if params.denominator else "")
    job = registry().launch(
        "run_family", label, run_family_job, total=params.simulate, params=params
    )
    return job.as_dict()


@router.get("/runs")
def list_runs() -> dict:
    return {"jobs": [j.as_dict() for j in registry().list()]}


@router.get("/runs/{job_id}")
def get_run(job_id: str) -> dict:
    job = registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job.as_dict()


@router.post("/alphas/{alpha_id}/undo")
def undo_mark(alpha_id: int, db: Session = Depends(get_db)) -> dict:
    """Revert an alpha to the status it held before the last transition.

    `x` is one keystroke and it removed a candidate permanently, which makes an
    operator hesitate over exactly the action the workflow wants to be cheap.
    The previous state is already in alpha_status_history, so undo is a read of
    that rather than any new bookkeeping.
    """
    from app.models.alphas import AlphaStatusHistory

    alpha = db.get(Alpha, alpha_id)
    if alpha is None:
        raise HTTPException(status_code=404, detail="no such alpha")
    last = (
        db.execute(
            select(AlphaStatusHistory)
            .where(AlphaStatusHistory.alpha_id == alpha_id)
            .order_by(AlphaStatusHistory.id.desc())
        )
        .scalars()
        .first()
    )
    if last is None or not last.from_status:
        raise HTTPException(status_code=422, detail="nothing to undo")
    transition_status(db, alpha, last.from_status, note="ui:undo")
    return {"alpha_id": alpha_id, "status": alpha.status}


@router.post("/alphas/{alpha_id}/mark")
def mark_alpha(alpha_id: int, payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    """Record what the HUMAN did with an alpha.

    ``submitted`` means the operator pasted it into BRAIN themselves. Nothing
    here contacts the platform — this only writes down a decision that has
    already been made, so hit-rate can tell "survived the filter" apart from
    "actually put forward".
    """
    action = payload.get("action")
    mapping = {
        "submitted": AlphaStatus.SUBMITTED.value,
        "discarded": AlphaStatus.ARCHIVED.value,
    }
    if action not in mapping:
        raise HTTPException(status_code=422, detail=f"action must be one of {sorted(mapping)}")

    alpha = db.get(Alpha, alpha_id)
    if alpha is None:
        raise HTTPException(status_code=404, detail="no such alpha")

    transition_status(db, alpha, mapping[action], note=payload.get("note") or f"ui:{action}")
    return {"alpha_id": alpha_id, "status": alpha.status}


@router.get("/correlation-matrix")
def correlation_matrix(db: Session = Depends(get_db)) -> dict:
    """Computes full empirical correlation matrix over submitted and promoted alphas."""
    from app.services.correlation import compute_correlation_matrix
    from app.services.pnl_storage import get_pnl_store

    store = get_pnl_store()
    alphas = list(
        db.execute(
            select(Alpha).where(
                Alpha.status.in_([AlphaStatus.SUBMITTED.value, AlphaStatus.PASSED.value])
            )
        )
        .scalars()
        .all()
    )
    a_ids = [a.id for a in alphas]
    valid_ids, dates, mat = store.get_aligned_matrix(a_ids, min_overlap=30)
    if not valid_ids:
        return {"alpha_ids": [], "matrix": [], "common_days": 0}

    corr = compute_correlation_matrix(mat)
    return {
        "alpha_ids": valid_ids,
        "matrix": corr.tolist(),
        "common_days": len(dates),
    }


@router.get("/lineage/{alpha_id}")
def lineage_tree(alpha_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Walks the lineage tree for an alpha using recursive CTE."""
    from sqlalchemy import text

    LINEAGE_CTE = text("""
        WITH RECURSIVE lineage(id, expression, parent_id, generation, mutation_type, depth) AS (
            SELECT id, expression, parent_id, generation, mutation_type, 0 FROM alphas WHERE id = :root
            UNION ALL
            SELECT a.id, a.expression, a.parent_id, a.generation, a.mutation_type, l.depth + 1
            FROM alphas a JOIN lineage l ON a.id = l.parent_id
        )
        SELECT id, expression, parent_id, generation, mutation_type, depth FROM lineage ORDER BY depth DESC
    """)
    rows = db.execute(LINEAGE_CTE, {"root": alpha_id}).mappings().all()
    return [dict(r) for r in rows]


@router.get("/telemetry")
def funnel_telemetry(db: Session = Depends(get_db)) -> dict:
    """Telemetry report of funnel drop-offs across statistical gates."""
    total = db.scalar(select(func.count(Alpha.id))) or 0
    valid = db.scalar(select(func.count(Alpha.id)).where(Alpha.is_valid.is_(True))) or 0
    simulated = db.scalar(select(func.count(AlphaMetric.id))) or 0
    passing_brain = (
        db.scalar(select(func.count(AlphaMetric.id)).where(AlphaMetric.passed_all_checks.is_(True)))
        or 0
    )
    submitted = (
        db.scalar(select(func.count(Alpha.id)).where(Alpha.status == AlphaStatus.SUBMITTED.value))
        or 0
    )

    promoted_count = 0
    dsr_gate_count = 0
    cold_start_count = 0

    for family in _families(db):
        for v in evaluate(db, family):
            if v.promoted:
                promoted_count += 1
            if v.gate_mode == "DSR":
                dsr_gate_count += 1
            else:
                cold_start_count += 1

    return {
        "funnel": {
            "generated": total,
            "valid_syntax": valid,
            "simulated": simulated,
            "passed_brain_checks": passing_brain,
            "promoted_shortlist": promoted_count,
            "submitted_portfolio": submitted,
        },
        "gates_active": {
            "dsr_mode_evaluations": dsr_gate_count,
            "cold_start_evaluations": cold_start_count,
        },
    }

