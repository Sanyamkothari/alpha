"""Service for retrieving field crowding snapshots and point-in-time metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fields import DataField, DataFieldSnapshot


@dataclass
class FieldCrowding:
    field_code: str
    region: str
    delay: int
    universe: str
    as_of_date: date | None
    user_count: int | None
    alpha_count: int | None
    coverage: float | None
    category: str
    field_type: str


def get_field_crowding(
    db: Session,
    field_code: str,
    as_of_date: date | None = None,
    *,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
) -> FieldCrowding | None:
    """Fetch field crowding metrics as of a specific date (or latest current state)."""
    if as_of_date is not None:
        snap = (
            db.execute(
                select(DataFieldSnapshot)
                .where(
                    DataFieldSnapshot.field_code == field_code,
                    DataFieldSnapshot.region == region,
                    DataFieldSnapshot.delay == delay,
                    DataFieldSnapshot.universe == universe,
                    DataFieldSnapshot.as_of_date <= as_of_date,
                )
                .order_by(DataFieldSnapshot.as_of_date.desc())
            )
            .scalars()
            .first()
        )

        if snap is not None:
            return FieldCrowding(
                field_code=snap.field_code,
                region=snap.region,
                delay=snap.delay,
                universe=snap.universe,
                as_of_date=snap.as_of_date,
                user_count=snap.user_count,
                alpha_count=snap.alpha_count,
                coverage=snap.coverage,
                category=snap.category,
                field_type=snap.field_type,
            )

    # Fallback to current state
    curr = (
        db.execute(
            select(DataField).where(
                DataField.field_code == field_code,
                DataField.region == region,
                DataField.delay == delay,
                DataField.universe == universe,
            )
        )
        .scalars()
        .first()
    )

    if curr is not None:
        return FieldCrowding(
            field_code=curr.field_code,
            region=curr.region,
            delay=curr.delay,
            universe=curr.universe,
            as_of_date=None,
            user_count=curr.user_count,
            alpha_count=curr.alpha_count,
            coverage=curr.coverage,
            category=curr.category,
            field_type=curr.field_type,
        )

    return None
