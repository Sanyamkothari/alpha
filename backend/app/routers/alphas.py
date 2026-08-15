"""Modules 6 + 7 read/write API — Alpha Library + Result Importer + leaderboard.

All writes here target OUR database only. Storing an alpha is gated on the
validator (invalid expressions are rejected, never persisted). Importing a result
parses a human-pasted BRAIN block — it never contacts the platform.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.alphas import Alpha, AlphaStatusHistory
from app.models.enums import ImportSource, OutcomeSource, PlatformOutcome
from app.models.results import AlphaMetric, SimulationImport
from app.services.alpha_library import (
    AlphaSettings,
    InvalidAlphaError,
    create_alpha,
    transition_status,
)
from app.services.result_import import import_result

router = APIRouter(prefix="/alphas", tags=["alphas"])

_SORTABLE = {
    "sharpe",
    "fitness",
    "turnover",
    "returns",
    "margin_bps",
    "drawdown",
    "complexity_score",
}


# --------------------------------------------------------------------- schemas


class SettingsIn(BaseModel):
    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    neutralization: str | None = None
    decay: int | None = None
    truncation: float | None = None


class AlphaCreateIn(BaseModel):
    expression: str
    settings: SettingsIn = SettingsIn()
    family_key: str | None = None
    parent_id: int | None = None
    mutation_type: str | None = None
    source: str = "user"
    comments: str | None = None


class IssueOut(BaseModel):
    code: str
    message: str
    span: tuple[int, int] | None = None


class AlphaOut(BaseModel):
    id: int
    expression: str
    normalized_expression: str | None
    status: str
    source: str
    is_valid: bool
    complexity_score: float | None
    region: str
    universe: str
    delay: int
    generation: int
    platform_outcome: str | None = None
    outcome_date: date | None = None
    outcome_note: str | None = None
    outcome_source: str | None = None

    model_config = {"from_attributes": True}


class MetricsOut(BaseModel):
    sharpe: float | None
    fitness: float | None
    turnover: float | None
    returns: float | None
    margin_bps: float | None
    drawdown: float | None
    long_count: int | None
    short_count: int | None
    passed_all_checks: bool | None

    model_config = {"from_attributes": True}


class StatusHistoryOut(BaseModel):
    from_status: str | None
    to_status: str
    note: str | None

    model_config = {"from_attributes": True}


class AlphaCreateOut(BaseModel):
    created: bool  # False => an identical alpha already existed
    alpha: AlphaOut
    warnings: list[IssueOut]


class AlphaDetailOut(AlphaOut):
    feature_json: dict | None = None
    validation_warnings: list | None = None
    latest_metrics: MetricsOut | None = None
    status_history: list[StatusHistoryOut] = []


class StatusIn(BaseModel):
    to_status: str
    note: str | None = None


class OutcomeIn(BaseModel):
    platform_outcome: PlatformOutcome
    outcome_date: date | None = None
    outcome_note: str | None = None
    outcome_source: OutcomeSource = OutcomeSource.MANUAL


class OutcomeOut(BaseModel):
    alpha_id: int
    platform_outcome: str
    outcome_date: date | None
    outcome_note: str | None
    outcome_source: str


class ResultIn(BaseModel):
    raw: str | dict
    # Constrained to the ImportSource enum so a bad value is a 422, not a DB-CHECK 500.
    source: ImportSource = ImportSource.PASTE


class ResultOut(BaseModel):
    new_status: str
    import_id: int
    metrics: MetricsOut


class LeaderboardRow(BaseModel):
    alpha_id: int
    expression: str
    status: str
    complexity_score: float | None
    sharpe: float | None
    fitness: float | None
    turnover: float | None
    returns: float | None


# --------------------------------------------------------------------- helpers


def _get_alpha(db: Session, alpha_id: int) -> Alpha:
    alpha = db.get(Alpha, alpha_id)
    if alpha is None:
        raise HTTPException(status_code=404, detail="alpha not found")
    return alpha


def _latest_metric(db: Session, alpha_id: int) -> AlphaMetric | None:
    return db.execute(
        select(AlphaMetric)
        .join(SimulationImport, AlphaMetric.simulation_import_id == SimulationImport.id)
        .where(AlphaMetric.alpha_id == alpha_id, SimulationImport.is_latest.is_(True))
    ).scalar_one_or_none()


# ---------------------------------------------------------------- create / list


@router.post("", response_model=AlphaCreateOut, status_code=201)
def create(
    body: AlphaCreateIn, response: Response, db: Session = Depends(get_db)
) -> AlphaCreateOut:
    settings = AlphaSettings(**body.settings.model_dump())
    try:
        result = create_alpha(
            db,
            body.expression,
            settings,
            family_key=body.family_key,
            parent_id=body.parent_id,
            mutation_type=body.mutation_type,
            source=body.source,
            comments=body.comments,
        )
    except InvalidAlphaError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "expression failed validation; not stored",
                "errors": [vars(e) for e in exc.result.errors],
            },
        ) from exc
    # 201 only on a real insert; a dedup hit is an idempotent no-op -> 200.
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return AlphaCreateOut(
        created=result.created,
        alpha=AlphaOut.model_validate(result.alpha),
        warnings=[IssueOut(**vars(w)) for w in result.validation.warnings],
    )


@router.get("", response_model=list[AlphaOut])
def list_alphas(
    db: Session = Depends(get_db),
    status: str | None = None,
    source: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[AlphaOut]:
    query = select(Alpha)
    if status:
        query = query.where(Alpha.status == status)
    if source:
        query = query.where(Alpha.source == source)
    query = query.order_by(Alpha.created_at.desc()).offset(offset).limit(limit)
    return [AlphaOut.model_validate(a) for a in db.execute(query).scalars()]


@router.get("/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(
    db: Session = Depends(get_db),
    sort: str = Query("sharpe"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
) -> list[LeaderboardRow]:
    if sort not in _SORTABLE:
        raise HTTPException(status_code=400, detail=f"sort must be one of {sorted(_SORTABLE)}")
    sort_col = getattr(Alpha if sort == "complexity_score" else AlphaMetric, sort)
    query = (
        select(AlphaMetric, Alpha)
        .join(SimulationImport, AlphaMetric.simulation_import_id == SimulationImport.id)
        .join(Alpha, AlphaMetric.alpha_id == Alpha.id)
        .where(SimulationImport.is_latest.is_(True), sort_col.is_not(None))
        .order_by(sort_col.desc() if order == "desc" else sort_col.asc())
        .limit(limit)
    )
    rows: list[LeaderboardRow] = []
    for metric, alpha in db.execute(query).all():
        rows.append(
            LeaderboardRow(
                alpha_id=alpha.id,
                expression=alpha.expression,
                status=alpha.status,
                complexity_score=alpha.complexity_score,
                sharpe=metric.sharpe,
                fitness=metric.fitness,
                turnover=metric.turnover,
                returns=metric.returns,
            )
        )
    return rows


@router.get("/{alpha_id}", response_model=AlphaDetailOut)
def get_alpha(alpha_id: int, db: Session = Depends(get_db)) -> AlphaDetailOut:
    alpha = _get_alpha(db, alpha_id)
    metric = _latest_metric(db, alpha_id)
    history = sorted(alpha.status_history, key=lambda h: h.id)
    detail = AlphaDetailOut.model_validate(alpha)
    detail.feature_json = alpha.feature_json
    detail.validation_warnings = alpha.validation_warnings
    detail.latest_metrics = MetricsOut.model_validate(metric) if metric else None
    detail.status_history = [StatusHistoryOut.model_validate(h) for h in history]
    return detail


# ------------------------------------------------------ status + result import


@router.post("/{alpha_id}/status", response_model=AlphaOut)
def set_status(alpha_id: int, body: StatusIn, db: Session = Depends(get_db)) -> AlphaOut:
    alpha = _get_alpha(db, alpha_id)
    try:
        transition_status(db, alpha, body.to_status, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AlphaOut.model_validate(alpha)


@router.post("/{alpha_id}/outcome", response_model=OutcomeOut)
def set_outcome(alpha_id: int, body: OutcomeIn, db: Session = Depends(get_db)) -> OutcomeOut:
    """Record a WorldQuant BRAIN post-submission platform review outcome."""
    alpha = _get_alpha(db, alpha_id)
    outcome_val = body.platform_outcome.value
    source_val = body.outcome_source.value
    outcome_dt = body.outcome_date or date.today()

    alpha.platform_outcome = outcome_val
    alpha.outcome_date = outcome_dt
    alpha.outcome_note = body.outcome_note
    alpha.outcome_source = source_val

    # Audit trail in alpha_status_history
    hist_note = f"outcome:{outcome_val}" + (f": {body.outcome_note}" if body.outcome_note else "")
    entry = AlphaStatusHistory(
        alpha_id=alpha.id,
        from_status=alpha.status,
        to_status=alpha.status,
        note=hist_note,
    )
    db.add(entry)
    db.flush()

    return OutcomeOut(
        alpha_id=alpha.id,
        platform_outcome=outcome_val,
        outcome_date=outcome_dt,
        outcome_note=body.outcome_note,
        outcome_source=source_val,
    )


@router.post("/{alpha_id}/results", response_model=ResultOut, status_code=201)
def import_result_endpoint(
    alpha_id: int, body: ResultIn, db: Session = Depends(get_db)
) -> ResultOut:
    alpha = _get_alpha(db, alpha_id)
    outcome = import_result(db, alpha, body.raw, source=body.source.value)
    return ResultOut(
        new_status=outcome.new_status,
        import_id=outcome.simulation_import.id,
        metrics=MetricsOut.model_validate(outcome.metrics),
    )
