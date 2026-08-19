"""Salvage correlation-rejected alphas as residual candidates (C2).

An alpha rejected at the correlation gate cost a simulation to learn "too similar to
something we already hold". ``regression_neut`` returns the component of that signal
which the colliding factor cannot explain, so the rejection becomes a candidate
rather than a dead end — and, because it is orthogonal by construction rather than by
rejection sampling, it attacks the constraint STRATEGY.md §2 calls the hard part.

    python -m scripts.run_orthogonalization --family <key>
    python -m scripts.run_orthogonalization --alpha 412 --colliding 87
    python -m scripts.run_orthogonalization --family <key> --dry-run

Emits candidates only. Nothing here simulates, and nothing here submits.
"""

from __future__ import annotations

import argparse

import structlog

from app.db.session import session_scope
from app.services.orthogonalization import generate_residuals_for_alpha
from app.services.plateau import evaluate

log = structlog.get_logger("run_orthogonalization")


def salvage_family(family_key: str, *, dry_run: bool = False, limit: int = 5) -> dict[str, int]:
    """Generate residuals for every correlation-rejected point in a family."""
    stats = {"rejected": 0, "salvaged": 0, "created": 0}

    with session_scope() as db:
        verdicts = evaluate(db, family_key)
        rejected = [v for v in verdicts if v.is_correlated]
        stats["rejected"] = len(rejected)

        # Highest Sharpe first: a residual is only worth simulating if the signal it
        # is carved out of was worth something to begin with.
        rejected.sort(key=lambda v: v.sharpe or 0.0, reverse=True)

        for verdict in rejected[:limit]:
            created = generate_residuals_for_alpha(db, verdict.alpha_id)
            if created:
                stats["salvaged"] += 1
                stats["created"] += len(created)
            print(
                f"  alpha #{verdict.alpha_id} (Sharpe {verdict.sharpe:.2f}) "
                f"-> {len(created)} residual candidate(s)"
            )

        if dry_run:
            db.rollback()

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family", help="salvage every correlation-rejected point in this family")
    ap.add_argument("--alpha", type=int, help="salvage one alpha by id")
    ap.add_argument("--colliding", type=int, help="alpha id to also neutralise against (Tier 2)")
    ap.add_argument("--limit", type=int, default=5, help="max alphas to salvage per family")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    if not args.family and not args.alpha:
        ap.error("pass --family or --alpha")

    if args.alpha:
        with session_scope() as db:
            created = generate_residuals_for_alpha(
                db, args.alpha, colliding_alpha_id=args.colliding
            )
            if args.dry_run:
                db.rollback()
            print(f"alpha #{args.alpha} -> {len(created)} residual candidate(s)")
        return 0

    stats = salvage_family(args.family, dry_run=args.dry_run, limit=args.limit)
    print(
        f"correlation-rejected={stats['rejected']} salvaged={stats['salvaged']} "
        f"candidates={stats['created']}"
        + (" (dry run — nothing written)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
