"""Seed the small lookup tables from the enums.

``failure_reasons`` went away with the AI-Critic module; only the field-category
lookup remains.
"""

from __future__ import annotations

import structlog

from app.db import session_scope
from app.models.enums import FieldCategory
from app.models.fields import Category

log = structlog.get_logger("seeds.lookups")


def run() -> dict[str, int]:
    with session_scope() as db:
        existing_cats = {c.name for c in db.query(Category).all()}
        for cat in FieldCategory:
            if cat.value not in existing_cats:
                db.add(Category(name=cat.value))

    with session_scope() as db:
        counts = {"categories": db.query(Category).count()}
    log.info("lookups_seeded", **counts)
    return counts


if __name__ == "__main__":
    print(run())
