"""Import the alphas that already exist on BRAIN into the local database.

The platform holds every alpha ever simulated on the account. Those are real,
already-paid-for results: they tell you what has been tried and what it scored,
which is the starting point for the per-dataset hit-rate table (STRATEGY.md §4
Rule 1) without generating anything new.

Alphas whose expression the local validator rejects are still recorded — the
result is real history even when the expression uses an operator or field the
seeded knowledge base does not know about yet. They are stored with
``is_valid=False`` and their validation errors, so a later catalog refresh can
re-validate them.

Usage:
    python -m scripts.import_brain_alphas [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.models.alphas import Alpha
from app.models.enums import ImportSource
from app.services.alpha_library import AlphaSettings, expression_hash, stamp_alpha_field_snapshots
from app.services.brain import BrainClient
from app.services.brain.client import normalize_is_block
from app.services.result_import import import_result
from app.services.validation import validate_expression

log = structlog.get_logger("import_brain_alphas")


def _settings_from(payload: dict) -> AlphaSettings:
    s = payload.get("settings") or {}
    return AlphaSettings(
        region=s.get("region", "USA"),
        universe=s.get("universe", "TOP3000"),
        delay=int(s.get("delay", 1)),
        neutralization=s.get("neutralization"),
        decay=s.get("decay"),
        truncation=s.get("truncation"),
    )


def run(limit: int | None = None, dry_run: bool = False) -> dict[str, int]:
    counts = {"seen": 0, "imported": 0, "skipped_existing": 0, "no_metrics": 0, "invalid": 0}

    with BrainClient() as brain:
        remote = list(brain.own_alphas())
    log.info("fetched_remote_alphas", count=len(remote))
    if limit:
        remote = remote[:limit]

    for payload in remote:
        counts["seen"] += 1
        expression = (payload.get("regular") or {}).get("code") or payload.get("regular")
        if not isinstance(expression, str) or not expression.strip():
            continue

        is_block = payload.get("is")
        if not isinstance(is_block, dict) or not is_block:
            counts["no_metrics"] += 1
            continue

        settings_obj = _settings_from(payload)
        digest = expression_hash(expression.strip(), settings_obj)

        if dry_run:
            continue

        with session_scope() as db:
            existing = db.execute(
                select(Alpha).where(Alpha.expression_hash == digest)
            ).scalar_one_or_none()
            if existing is not None:
                counts["skipped_existing"] += 1
                continue

            result = validate_expression(
                db,
                expression,
                region=settings_obj.region,
                delay=settings_obj.delay,
                universe=settings_obj.universe,
            )
            if not result.valid:
                counts["invalid"] += 1

            alpha = Alpha(
                expression=expression,
                normalized_expression=result.normalized_expression,
                expression_hash=digest,
                status="untested",
                source="brain_import",
                region=settings_obj.region,
                universe=settings_obj.universe,
                delay=settings_obj.delay,
                neutralization=settings_obj.neutralization,
                decay=settings_obj.decay,
                truncation=settings_obj.truncation,
                is_valid=result.valid,
                validation_errors=[e.message for e in result.errors] or None,
                complexity_score=result.complexity_score,
                feature_json=result.features,
                comments=f"imported from BRAIN alpha {payload.get('id')}",
            )
            db.add(alpha)
            db.flush()

            stamp_alpha_field_snapshots(db, alpha, result.features.get("distinct_fields") if result.features else None)

            import_result(
                db,
                alpha,
                normalize_is_block(is_block),
                source=ImportSource.BRAIN_API.value,
            )
            counts["imported"] += 1

    log.info("import_complete", **counts)
    return counts


from scripts._cli import cli_main


@cli_main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(run(limit=args.limit, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())

