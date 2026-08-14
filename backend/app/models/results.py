"""Module 7 — Simulation Result Importer.

``simulation_imports.raw_payload`` is the provenance / source-of-truth (whatever
the human pasted or the read-only GET returned). ``alpha_metrics`` is the hybrid
parsed projection: typed canonical columns for the metrics we reason over, plus
``extra``/``checks`` JSON for everything else. NOTE: Margin is stored in **bps**.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import JSONType
from app.models._common import Base, IdMixin, TimestampMixin, enum_check
from app.models.enums import ImportSource


class SimulationImport(IdMixin, TimestampMixin, Base):
    __tablename__ = "simulation_imports"

    alpha_id: Mapped[int] = mapped_column(
        ForeignKey("alphas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=ImportSource.PASTE)
    raw_payload: Mapped[dict | None] = mapped_column(JSONType)
    # Full history is kept; the latest run per alpha is flagged for leaderboards.
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)

    metrics: Mapped[AlphaMetric | None] = relationship(
        back_populates="simulation_import",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (enum_check("source", ImportSource),)


class AlphaMetric(IdMixin, TimestampMixin, Base):
    __tablename__ = "alpha_metrics"

    simulation_import_id: Mapped[int] = mapped_column(
        ForeignKey("simulation_imports.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Denormalized for easy leaderboard/critic queries.
    alpha_id: Mapped[int] = mapped_column(
        ForeignKey("alphas.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ---- Typed canonical metrics ----
    sharpe: Mapped[float | None] = mapped_column(Float)
    fitness: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    returns: Mapped[float | None] = mapped_column(Float)
    margin_bps: Mapped[float | None] = mapped_column(Float)  # margin in basis points
    subuniverse_sharpe: Mapped[float | None] = mapped_column(Float)
    self_correlation: Mapped[float | None] = mapped_column(Float)
    drawdown: Mapped[float | None] = mapped_column(Float)
    long_count: Mapped[int | None] = mapped_column(Integer)
    short_count: Mapped[int | None] = mapped_column(Integer)

    # ---- Flexible overflow ----
    extra: Mapped[dict | None] = mapped_column(JSONType)
    checks: Mapped[list | None] = mapped_column(JSONType)
    passed_all_checks: Mapped[bool | None] = mapped_column(Boolean)

    simulation_import: Mapped[SimulationImport] = relationship(back_populates="metrics")
