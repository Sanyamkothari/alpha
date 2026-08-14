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

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import JSONType
from app.models._common import Base, IdMixin, TimestampMixin, enum_check
from app.models.enums import AlphaStatus, MutationType


class Alpha(IdMixin, TimestampMixin, Base):
    __tablename__ = "alphas"

    expression: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_expression: Mapped[str | None] = mapped_column(Text)
    # Hash of the normalized expression + settings — exact-dedup key. The
    # UniqueConstraint below already creates the backing index (no index=True).
    expression_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Grid provenance: which family this variant came from. The per-axis
    # coordinates (window, decay, neutralization, ...) live in ``feature_json``
    # under "grid"; this column exists so plateau queries can GROUP BY cheaply.
    family_key: Mapped[str | None] = mapped_column(String(128), index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AlphaStatus.UNTESTED)
    source: Mapped[str] = mapped_column(String(32), default="ai")  # ai | user | evolution

    # ---- BRAIN simulation settings block ----
    region: Mapped[str] = mapped_column(String(16), default="USA")
    universe: Mapped[str] = mapped_column(String(32), default="TOP3000")
    delay: Mapped[int] = mapped_column(Integer, default=1)
    neutralization: Mapped[str | None] = mapped_column(String(32))  # NONE/MARKET/SECTOR/...
    decay: Mapped[int | None] = mapped_column(Integer)
    truncation: Mapped[float | None] = mapped_column(Float)
    settings_extra: Mapped[dict | None] = mapped_column(JSONType)

    # ---- Validation result (the gate) ----
    is_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_errors: Mapped[list | None] = mapped_column(JSONType)
    validation_warnings: Mapped[list | None] = mapped_column(JSONType)

    # ---- Deterministic AST feature score (computed Phase 2; columns reserved in P0) ----
    # Queryable scalar for ranking/sorting; the per-feature breakdown lives in JSON
    # (depth, op_count, n_windows -> turnover proxy, interpretability, novelty).
    complexity_score: Mapped[float | None] = mapped_column(Float)
    feature_json: Mapped[dict | None] = mapped_column(JSONType)

    # ---- Genealogy ----
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("alphas.id"))
    generation: Mapped[int] = mapped_column(Integer, default=0)
    mutation_type: Mapped[str | None] = mapped_column(String(32))

    comments: Mapped[str | None] = mapped_column(Text)

    # ---- Relationships ----
    parent: Mapped[Alpha | None] = relationship(remote_side="Alpha.id", backref="children")
    status_history: Mapped[list[AlphaStatusHistory]] = relationship(
        back_populates="alpha", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("expression_hash", name="alpha_expression_hash"),
        enum_check("status", AlphaStatus),
        enum_check("mutation_type", MutationType),
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
