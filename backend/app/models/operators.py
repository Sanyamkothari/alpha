"""Module 2 — Operator Knowledge Base.

Seeded from ``operators/operators.yaml`` (Phase 0) and refreshed from the
read-only ``GET /operators`` endpoint (Phase 4). The deterministic validator
reads these tables as its source of truth — it never hardcodes operators.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._common import Base, IdMixin, TimestampMixin, enum_check
from app.models.enums import ArgType, OperatorCategory, ReturnType


class Operator(IdMixin, TimestampMixin, Base):
    __tablename__ = "operators"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    returns: Mapped[str] = mapped_column(String(16), nullable=False, default=ReturnType.MATRIX)
    restrictions: Mapped[str | None] = mapped_column(Text)
    typical_usage: Mapped[str | None] = mapped_column(Text)
    # Some operators unlock only at higher BRAIN levels — warn, don't hard-fail.
    scope: Mapped[str | None] = mapped_column(String(32))
    is_seeded: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)

    arguments: Mapped[list[OperatorArgument]] = relationship(
        back_populates="operator",
        cascade="all, delete-orphan",
        order_by="OperatorArgument.position",
    )
    examples: Mapped[list[OperatorExample]] = relationship(
        back_populates="operator", cascade="all, delete-orphan"
    )
    compatibility: Mapped[list[OperatorCompatibility]] = relationship(
        back_populates="operator", cascade="all, delete-orphan"
    )

    __table_args__ = (
        enum_check("category", OperatorCategory),
        enum_check("returns", ReturnType),
    )


class OperatorArgument(IdMixin, Base):
    __tablename__ = "operator_arguments"

    operator_id: Mapped[int] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    arg_type: Mapped[str] = mapped_column(String(16), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    # True if this is a lookback-days window argument (validator enforces the band).
    is_window: Mapped[bool] = mapped_column(Boolean, default=False)
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)

    operator: Mapped[Operator] = relationship(back_populates="arguments")

    __table_args__ = (
        UniqueConstraint("operator_id", "position", name="operator_arg_position"),
        enum_check("arg_type", ArgType),
    )


class OperatorExample(IdMixin, Base):
    __tablename__ = "operator_examples"

    operator_id: Mapped[int] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False
    )
    expr: Mapped[str] = mapped_column(Text, nullable=False)
    is_good: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)

    operator: Mapped[Operator] = relationship(back_populates="examples")


class OperatorCompatibility(IdMixin, Base):
    __tablename__ = "operator_compatibility"

    operator_id: Mapped[int] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False
    )
    other_operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 'wraps_well' | 'avoid_nesting' | 'alternative_of'
    relation: Mapped[str] = mapped_column(String(32), nullable=False)

    operator: Mapped[Operator] = relationship(back_populates="compatibility")

    __table_args__ = (
        UniqueConstraint(
            "operator_id", "other_operator_name", "relation", name="operator_compat_edge"
        ),
    )
