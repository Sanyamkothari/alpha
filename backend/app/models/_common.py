"""Shared model building blocks."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow

__all__ = ["Base", "TimestampMixin", "IdMixin", "enum_check", "utcnow"]


class IdMixin:
    """Surrogate integer primary key."""

    id: Mapped[int] = mapped_column(primary_key=True)


def enum_check(column: str, enum_cls: type[StrEnum]) -> CheckConstraint:
    """A CHECK constraint that restricts ``column`` to the enum's values.

    Combined with the naming convention this renders as
    ``ck_<table>_<column>_valid`` in migrations.
    """
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=f"{column}_valid")
