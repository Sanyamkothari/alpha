"""Backfill ``feature_json.subtree_hashes`` for alphas created before C1.

The novelty prior scores a candidate against the subtrees already present in the
corpus. Alphas stored before subtree extraction existed have no hashes, so they
contribute nothing to the frequency table and the prior starts blind on a history
we already have.

Idempotent: alphas that already carry hashes are skipped unless ``--force``.

    python -m scripts.backfill_subtree_hashes
    python -m scripts.backfill_subtree_hashes --force --dry-run
"""

from __future__ import annotations

import argparse

import structlog
from sqlalchemy import select

from app.db.session import session_scope
from app.models.alphas import Alpha
from app.validator import ValidatorKB
from app.validator.features import all_subtree_hashes
from app.validator.parser import parse

log = structlog.get_logger("backfill_subtree_hashes")


def backfill(*, force: bool = False, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "updated": 0, "skipped": 0, "failed": 0}

    with session_scope() as db:
        kb = ValidatorKB.from_session(db)
        alphas = db.execute(select(Alpha)).scalars().all()

        for alpha in alphas:
            stats["scanned"] += 1
            features = dict(alpha.feature_json or {})
            if features.get("subtree_hashes") and not force:
                stats["skipped"] += 1
                continue
            try:
                ast = parse(alpha.normalized_expression or alpha.expression)
                features["subtree_hashes"] = all_subtree_hashes(ast, kb)
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the sweep
                log.warning("subtree_backfill_failed", alpha_id=alpha.id, error=str(exc))
                stats["failed"] += 1
                continue

            if not dry_run:
                alpha.feature_json = features
            stats["updated"] += 1

        if dry_run:
            db.rollback()

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-extract even when hashes exist")
    ap.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    args = ap.parse_args()

    stats = backfill(force=args.force, dry_run=args.dry_run)
    print(
        f"scanned={stats['scanned']} updated={stats['updated']} "
        f"skipped={stats['skipped']} failed={stats['failed']}"
        + (" (dry run — nothing written)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
