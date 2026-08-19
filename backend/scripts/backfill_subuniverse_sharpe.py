"""Recover `subuniverse_sharpe` from the stored check payloads.

`alpha_metrics.subuniverse_sharpe` is populated on 4 rows. The figure is actually
present on 565 — BRAIN reports it as the ``value`` of the ``LOW_SUB_UNIVERSE_SHARPE``
entry inside ``checks``, and the importer only ever looked for a top-level
``subUniverseSharpe`` key that BRAIN does not send there.

The cost of that gap: the question "does low turnover cost us sub-universe Sharpe?"
came back CANNOT DETERMINE for want of data we had all along — and that question gates
whether the whole low-turnover idea is worth pursuing, because the sub-universe check
is what silently sank an entire family.

This is a backfill for history. ``result_import`` has been fixed so new results carry
the figure without it.

    python -m scripts.backfill_subuniverse_sharpe --dry-run
    python -m scripts.backfill_subuniverse_sharpe
"""

from __future__ import annotations

import argparse

import structlog
from sqlalchemy import select

from app.db.session import session_scope
from app.models.results import AlphaMetric

log = structlog.get_logger("backfill_subuniverse")

CHECK = "LOW_SUB_UNIVERSE_SHARPE"


def subuniverse_from_checks(checks: list | None) -> float | None:
    """The sub-universe Sharpe BRAIN reported, or None when it did not report one."""
    for entry in checks or []:
        if isinstance(entry, dict) and entry.get("name") == CHECK:
            value = entry.get("value")
            if isinstance(value, (int, float)):
                return float(value)
    return None


def backfill(*, dry_run: bool = False, force: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "recovered": 0, "already_set": 0, "no_value": 0}

    with session_scope() as db:
        for metric in db.execute(select(AlphaMetric)).scalars():
            stats["scanned"] += 1
            if metric.subuniverse_sharpe is not None and not force:
                stats["already_set"] += 1
                continue
            value = subuniverse_from_checks(metric.checks)
            if value is None:
                stats["no_value"] += 1
                continue
            if not dry_run:
                metric.subuniverse_sharpe = value
            stats["recovered"] += 1

        if dry_run:
            db.rollback()

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    ap.add_argument("--force", action="store_true", help="overwrite values already stored")
    args = ap.parse_args()

    stats = backfill(dry_run=args.dry_run, force=args.force)
    print(
        f"scanned={stats['scanned']} recovered={stats['recovered']} "
        f"already_set={stats['already_set']} no_value={stats['no_value']}"
        + (" (dry run — nothing written)" if args.dry_run else "")
    )
    if stats["recovered"]:
        print("\nRe-run `python -m scripts.answer_open_questions` — Q2 should now be answerable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
