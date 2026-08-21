"""Alpha Library — a formula *is* an alpha row.

Central entity of the system. Holds the expression, its validation result,
the BRAIN simulation settings block, status lifecycle, and genealogy
(self-referential parent + generation + mutation_type). Only validator-passing
expressions are ever inserted (the validator is the hard gate).

``family_key`` groups every variant the constructor emitted from one
(field, mechanism) seed. Plateau analysis — judging a candidate by its grid
neighbours rather than its own peak — reads this column, so it is indexed.
See STRATEGY.md Rule 2 and Rule 5.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.campaigns import CampaignTask

from app.db.types import JSONType
from app.models._common import Base, IdMixin, TimestampMixin, enum_check
from app.models.enums import (
    AlphaStatus,
    MutationType,
    OutcomeSource,
    PlatformOutcome,
    ProductionStatus,
    SubmissionResult,
)


class Alpha(IdMixin, TimestampMixin, Base):
    """Core Alpha model."""

    __tablename__ = "alphas"

    # ---- Identity & Expression ----
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_expression: Mapped[str | None] = mapped_column(Text)
    expression_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    # ---- Lifecycle & Lineage ----
    status: Mapped[str] = mapped_column(
        String(16), default=AlphaStatus.UNTESTED.value, nullable=False
    )
    family_key: Mapped[str | None] = mapped_column(String(256), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("alphas.id", ondelete="SET NULL"))
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mutation_type: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32), default="user", nullable=False)

    # ---- Settings ----
    region: Mapped[str] = mapped_column(String(16), default="USA", nullable=False)
    universe: Mapped[str] = mapped_column(String(32), default="TOP3000", nullable=False)
    delay: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    neutralization: Mapped[str | None] = mapped_column(String(32))
    decay: Mapped[int | None] = mapped_column(Integer)
    truncation: Mapped[float | None] = mapped_column(Float)

    # ---- Validation Cache ----
    is_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_errors: Mapped[list[str] | None] = mapped_column(JSONType)
    validation_warnings: Mapped[list[str] | None] = mapped_column(JSONType)
    complexity_score: Mapped[float | None] = mapped_column(Float)
    feature_json: Mapped[dict | None] = mapped_column(JSONType)

    # ---- Notes ----
    comments: Mapped[str | None] = mapped_column(Text)

    # ---- Platform Outcome (Derived from submission_attempts) ----
    platform_outcome: Mapped[str | None] = mapped_column(String(16), index=True)
    outcome_date: Mapped[date | None] = mapped_column(Date)
    outcome_note: Mapped[str | None] = mapped_column(Text)
    outcome_source: Mapped[str | None] = mapped_column(String(16))

    # ---- Campaign & Budget Allocation (Phase 1) ----
    arm: Mapped[str | None] = mapped_column(String(32), index=True)
    campaign_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaign_tasks.id", ondelete="SET NULL")
    )

    # ---- Relationships ----
    parent: Mapped[Alpha | None] = relationship(remote_side="Alpha.id", backref="children")
    campaign_task: Mapped[CampaignTask | None] = relationship(
        "CampaignTask", back_populates="alphas"
    )
    status_history: Mapped[list[AlphaStatusHistory]] = relationship(
        back_populates="alpha", cascade="all, delete-orphan"
    )
    field_snapshots: Mapped[list[AlphaFieldSnapshot]] = relationship(
        back_populates="alpha", cascade="all, delete-orphan"
    )
    attempts: Mapped[list[SubmissionAttempt]] = relationship(
        back_populates="alpha",
        cascade="all, delete-orphan",
        order_by="SubmissionAttempt.attempted_at.desc()",
    )
    production_snapshots: Mapped[list[AlphaProductionSnapshot]] = relationship(
        back_populates="alpha",
        cascade="all, delete-orphan",
        order_by="AlphaProductionSnapshot.as_of_date.desc()",
    )

    __table_args__ = (
        UniqueConstraint("expression_hash", name="alpha_expression_hash"),
        enum_check("status", AlphaStatus),
        enum_check("mutation_type", MutationType),
        enum_check("platform_outcome", PlatformOutcome),
        enum_check("outcome_source", OutcomeSource),
        CheckConstraint(
            "arm IS NULL OR arm IN ('exploit', 'random_stratified', 'plateau_fill')",
            name="ck_alphas_arm_valid",
        ),
    )


class AlphaStatusHistory(IdMixin, TimestampMixin, Base):
    """Append-only status transitions."""

    __tablename__ = "alpha_status_history"

    alpha_id: Mapped[int] = mapped_column(
        ForeignKey("alphas.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    alpha: Mapped[Alpha] = relationship(back_populates="status_history")

    # The audit trail must hold only valid lifecycle states, like every other
    # status column in the schema (from_status nullable => NULL passes the CHECK).
    __table_args__ = (
        enum_check("from_status", AlphaStatus),
        enum_check("to_status", AlphaStatus),
    )


class AlphaFieldSnapshot(IdMixin, TimestampMixin, Base):
    """Point-in-time snapshot of field crowding when an alpha was registered."""

    __tablename__ = "alpha_field_snapshot"

    alpha_id: Mapped[int] = mapped_column(
        ForeignKey("alphas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    field_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_fields.id", ondelete="SET NULL"), nullable=True
    )
    user_count: Mapped[int | None] = mapped_column(Integer)
    alpha_count: Mapped[int | None] = mapped_column(Integer)
    coverage: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    is_approximate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    alpha: Mapped[Alpha] = relationship(back_populates="field_snapshots")

    __table_args__ = (UniqueConstraint("alpha_id", "field_code", name="alpha_field_unique"),)


class SubmissionAttempt(IdMixin, TimestampMixin, Base):
    """Record of an attempt to submit an alpha to BRAIN."""

    __tablename__ = "submission_attempts"

    alpha_id: Mapped[int] = mapped_column(
        ForeignKey("alphas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    result: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # 'submitted' | 'rejected' | NULL
    failed_check: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_recalled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    alpha: Mapped[Alpha] = relationship(back_populates="attempts")

    __table_args__ = (enum_check("result", SubmissionResult),)


class AlphaProductionSnapshot(IdMixin, TimestampMixin, Base):
    """Time-series production performance and payment snapshot for submitted alphas."""

    __tablename__ = "alpha_production_snapshots"

    alpha_id: Mapped[int] = mapped_column(
        ForeignKey("alphas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'in_production' | 'decommissioned'
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    returns: Mapped[float | None] = mapped_column(Float, nullable=True)
    payment_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    alpha: Mapped[Alpha] = relationship(back_populates="production_snapshots")

    __table_args__ = (
        UniqueConstraint("alpha_id", "as_of_date", name="uq_alpha_production_as_of"),
        enum_check("status", ProductionStatus),
    )


def submitted_alpha_filter():
    """The ONE predicate for 'this alpha was submitted to BRAIN'.

    Reads ``platform_outcome``, which ``sync_alpha_platform_outcome`` derives
    from ``submission_attempts``, or directly inspects pending ``submission_attempts``.
    """
    return or_(
        Alpha.platform_outcome == PlatformOutcome.SUBMITTED.value,
        Alpha.id.in_(
            select(SubmissionAttempt.alpha_id).where(
                SubmissionAttempt.result == SubmissionResult.SUBMITTED.value
            )
        ),
    )


def sync_alpha_platform_outcome(db, alpha_id: int) -> str | None:
    """Derive and refresh the alpha's platform_outcome column from its submission_attempts."""
    from sqlalchemy import select

    alpha = db.get(Alpha, alpha_id)
    if alpha is None:
        return None

    attempts = (
        db.execute(
            select(SubmissionAttempt)
            .where(SubmissionAttempt.alpha_id == alpha_id)
            .order_by(SubmissionAttempt.attempted_at.desc())
        )
        .scalars()
        .all()
    )

    # Check for API sync history (platform verified outcomes)
    api_history = (
        db.execute(
            select(AlphaStatusHistory)
            .where(
                AlphaStatusHistory.alpha_id == alpha_id,
                AlphaStatusHistory.note.like("outcome:%(api sync%"),
            )
            .order_by(AlphaStatusHistory.id.desc())
        )
        .scalars()
        .first()
    )

    outcome = None
    outcome_dt = None
    outcome_note = None
    outcome_src = None

    if api_history:
        note = api_history.note or ""
        outcome_part = note.split()[0].replace("outcome:", "")
        if outcome_part in {e.value for e in PlatformOutcome}:
            outcome = outcome_part
            outcome_dt = api_history.created_at.date() if api_history.created_at else None
            outcome_note = note
            outcome_src = OutcomeSource.API.value

    if not outcome:
        if any(a.result == SubmissionResult.SUBMITTED.value for a in attempts):
            outcome = PlatformOutcome.SUBMITTED.value
            sub_attempt = next(a for a in attempts if a.result == SubmissionResult.SUBMITTED.value)
            outcome_dt = sub_attempt.attempted_at.date() if sub_attempt.attempted_at else None
            outcome_note = sub_attempt.notes
            outcome_src = OutcomeSource.MANUAL.value
            if alpha.status != AlphaStatus.SUBMITTED.value:
                alpha.status = AlphaStatus.SUBMITTED.value
        elif attempts and all(a.result == SubmissionResult.REJECTED.value for a in attempts):
            outcome = PlatformOutcome.REJECTED.value
            latest = attempts[0]
            outcome_dt = latest.attempted_at.date() if latest.attempted_at else None
            outcome_note = (
                f"{latest.failed_check or 'REJECTED'}: {latest.check_detail or ''}".strip(": ")
            )
            outcome_src = OutcomeSource.MANUAL.value
        elif any(a.result is None for a in attempts):
            outcome = PlatformOutcome.PENDING.value

    alpha.platform_outcome = outcome
    alpha.outcome_date = outcome_dt
    alpha.outcome_note = outcome_note
    alpha.outcome_source = outcome_src
    db.flush()
    return outcome
