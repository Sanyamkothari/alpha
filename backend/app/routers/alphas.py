"""Modules 6 + 7 read/write API — Alpha Library + Result Importer + leaderboard.

All writes here target OUR database only. Storing an alpha is gated on the
validator (invalid expressions are rejected, never persisted). Importing a result
parses a human-pasted BRAIN block — it never contacts the platform.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.alphas import (
    Alpha,
    AlphaProductionSnapshot,
    AlphaStatusHistory,
    SubmissionAttempt,
    sync_alpha_platform_outcome,
)
from app.models.enums import (
    ImportSource,
    ProductionStatus,
)
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


class AttemptIn(BaseModel):
    result: str | None = None  # 'submitted' | 'rejected' | None (pending)
    failed_check: str | None = None
    check_detail: str | None = None
    notes: str | None = None
    is_recalled: bool = False
    attempted_at: datetime | None = None


class AttemptResolveIn(BaseModel):
    result: str  # 'submitted' | 'rejected'
    failed_check: str | None = None
    check_detail: str | None = None
    notes: str | None = None


class AttemptOut(BaseModel):
    id: int
    alpha_id: int
    attempted_at: datetime
    result: str | None
    failed_check: str | None
    check_detail: str | None
    notes: str | None
    is_recalled: bool
    expression: str | None = None
    family_key: str | None = None

    model_config = {"from_attributes": True}


class ProductionSnapshotIn(BaseModel):
    as_of_date: date
    status: ProductionStatus = ProductionStatus.IN_PRODUCTION
    pnl: float | None = None
    sharpe: float | None = None
    returns: float | None = None
    payment_amount: float | None = None
    note: str | None = None


class ProductionSnapshotOut(BaseModel):
    id: int
    alpha_id: int
    as_of_date: date
    status: str
    pnl: float | None
    sharpe: float | None
    returns: float | None
    payment_amount: float | None
    note: str | None

    model_config = {"from_attributes": True}


class AlphaDetailOut(AlphaOut):
    feature_json: dict | None = None
    validation_warnings: list | None = None
    latest_metrics: MetricsOut | None = None
    status_history: list[StatusHistoryOut] = []
    attempts: list[AttemptOut] = []
    production_snapshots: list[ProductionSnapshotOut] = []


class StatusIn(BaseModel):
    to_status: str
    note: str | None = None


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


@router.get("/attempts/unresolved", response_model=list[AttemptOut])
def list_unresolved_attempts(db: Session = Depends(get_db)) -> list[AttemptOut]:
    """List all pending/unresolved submission attempts across all alphas."""
    attempts = db.execute(
        select(SubmissionAttempt, Alpha)
        .join(Alpha, SubmissionAttempt.alpha_id == Alpha.id)
        .where(SubmissionAttempt.result.is_(None))
        .order_by(SubmissionAttempt.attempted_at.desc())
    ).all()

    out: list[AttemptOut] = []
    for att, alpha in attempts:
        row = AttemptOut.model_validate(att)
        row.expression = alpha.expression
        row.family_key = alpha.family_key
        out.append(row)
    return out


@router.post("/{alpha_id}/attempts", response_model=AttemptOut, status_code=201)
def create_submission_attempt(
    alpha_id: int, body: AttemptIn, db: Session = Depends(get_db)
) -> AttemptOut:
    """Record a submission attempt (or initialize a pending one upon expression copy)."""
    alpha = _get_alpha(db, alpha_id)

    attempt = SubmissionAttempt(
        alpha_id=alpha.id,
        attempted_at=body.attempted_at or datetime.utcnow(),
        result=body.result,
        failed_check=body.failed_check,
        check_detail=body.check_detail,
        notes=body.notes,
        is_recalled=body.is_recalled,
    )
    db.add(attempt)
    db.flush()

    # Sync derived platform_outcome on alpha
    sync_alpha_platform_outcome(db, alpha.id)

    # If resolved immediately, add status audit row
    if body.result:
        hist_note = (
            f"attempt:{body.result}"
            + (f": {body.failed_check}" if body.failed_check else "")
            + (f": {body.notes}" if body.notes else "")
        )
        db.add(
            AlphaStatusHistory(
                alpha_id=alpha.id,
                from_status=alpha.status,
                to_status=alpha.status,
                note=hist_note,
            )
        )
        db.flush()

    out = AttemptOut.model_validate(attempt)
    out.expression = alpha.expression
    out.family_key = alpha.family_key
    return out


@router.get("/{alpha_id}/attempts", response_model=list[AttemptOut])
def list_alpha_attempts(alpha_id: int, db: Session = Depends(get_db)) -> list[AttemptOut]:
    """List all submission attempts for a specific alpha."""
    alpha = _get_alpha(db, alpha_id)
    attempts = (
        db.execute(
            select(SubmissionAttempt)
            .where(SubmissionAttempt.alpha_id == alpha_id)
            .order_by(SubmissionAttempt.attempted_at.desc())
        )
        .scalars()
        .all()
    )
    out: list[AttemptOut] = []
    for att in attempts:
        row = AttemptOut.model_validate(att)
        row.expression = alpha.expression
        row.family_key = alpha.family_key
        out.append(row)
    return out


@router.patch("/attempts/{attempt_id}/resolve", response_model=AttemptOut)
def resolve_attempt(
    attempt_id: int, body: AttemptResolveIn, db: Session = Depends(get_db)
) -> AttemptOut:
    """Resolve a pending submission attempt as submitted or rejected."""
    attempt = db.get(SubmissionAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="attempt not found")

    attempt.result = body.result
    attempt.failed_check = body.failed_check
    attempt.check_detail = body.check_detail
    if body.notes:
        attempt.notes = body.notes

    alpha = _get_alpha(db, attempt.alpha_id)
    sync_alpha_platform_outcome(db, alpha.id)

    hist_note = (
        f"attempt:{body.result}"
        + (f": {body.failed_check}" if body.failed_check else "")
        + (f": {body.notes}" if body.notes else "")
    )
    db.add(
        AlphaStatusHistory(
            alpha_id=alpha.id,
            from_status=alpha.status,
            to_status=alpha.status,
            note=hist_note,
        )
    )
    db.flush()

    out = AttemptOut.model_validate(attempt)
    out.expression = alpha.expression
    out.family_key = alpha.family_key
    return out


@router.delete("/attempts/{attempt_id}", status_code=204)
def delete_attempt(attempt_id: int, db: Session = Depends(get_db)) -> None:
    """Dismiss/delete an attempt."""
    attempt = db.get(SubmissionAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="attempt not found")
    alpha_id = attempt.alpha_id
    db.delete(attempt)
    db.flush()
    sync_alpha_platform_outcome(db, alpha_id)


@router.post(
    "/{alpha_id}/production-snapshots", response_model=ProductionSnapshotOut, status_code=201
)
def add_production_snapshot(
    alpha_id: int, body: ProductionSnapshotIn, db: Session = Depends(get_db)
) -> ProductionSnapshotOut:
    """Append a point-in-time production snapshot tracking ongoing performance."""
    alpha = _get_alpha(db, alpha_id)

    # Check for existing snapshot on that date
    existing = db.execute(
        select(AlphaProductionSnapshot).where(
            AlphaProductionSnapshot.alpha_id == alpha_id,
            AlphaProductionSnapshot.as_of_date == body.as_of_date,
        )
    ).scalar_one_or_none()

    if existing:
        existing.status = body.status.value
        existing.pnl = body.pnl
        existing.sharpe = body.sharpe
        existing.returns = body.returns
        existing.payment_amount = body.payment_amount
        existing.note = body.note
        db.flush()
        return ProductionSnapshotOut.model_validate(existing)

    snap = AlphaProductionSnapshot(
        alpha_id=alpha.id,
        as_of_date=body.as_of_date,
        status=body.status.value,
        pnl=body.pnl,
        sharpe=body.sharpe,
        returns=body.returns,
        payment_amount=body.payment_amount,
        note=body.note,
    )
    db.add(snap)
    db.flush()
    return ProductionSnapshotOut.model_validate(snap)


@router.get("/{alpha_id}/production-snapshots", response_model=list[ProductionSnapshotOut])
def list_production_snapshots(
    alpha_id: int, db: Session = Depends(get_db)
) -> list[ProductionSnapshotOut]:
    """List all production snapshots for an alpha in chronological order."""
    _get_alpha(db, alpha_id)
    snaps = (
        db.execute(
            select(AlphaProductionSnapshot)
            .where(AlphaProductionSnapshot.alpha_id == alpha_id)
            .order_by(AlphaProductionSnapshot.as_of_date.desc())
        )
        .scalars()
        .all()
    )
    return [ProductionSnapshotOut.model_validate(s) for s in snaps]


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
