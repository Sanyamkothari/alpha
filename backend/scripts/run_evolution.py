"""Run genetic evolution over top-performing alphas (Phase 1).

Enforces a pre-flight diversity gate requiring seed parents across at least 3 distinct
operator families to prevent monoculture loops.

    python -m scripts.run_evolution --pop-size 30 --simulate 10
"""

from __future__ import annotations

import argparse
import sys

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import Candidate
from app.services.evolution import EvolutionConfig, evolve_generation
from app.services.simulation_runner import pending_alpha_ids, run_batch

log = structlog.get_logger("run_evolution")

MIN_DIVERSITY_FAMILIES = 3


def check_seed_diversity(parent_alphas: list[Alpha]) -> tuple[bool, set[str]]:
    """Check how many distinct time-series operator families exist in parent pool."""
    known_ops = {"ts_zscore", "ts_rank", "ts_delta", "ts_mean", "ts_decay_linear", "ts_std_dev", "ts_quantile", "ts_corr"}
    found = set()
    for a in parent_alphas:
        expr = a.expression or ""
        for op in known_ops:
            if op in expr:
                found.add(op)
    return len(found) >= MIN_DIVERSITY_FAMILIES, found


def save(candidates: list[Candidate]) -> dict[str, int]:
    counts = {"created": 0, "duplicate": 0, "invalid": 0}
    with session_scope() as db:
        for cand in candidates:
            try:
                res = create_alpha(
                    db,
                    cand.expression,
                    cand.settings,
                    family_key=cand.family_key,
                    grid=cand.grid,
                    parent_id=cand.grid.get("parent_id"),
                    mutation_type=cand.grid.get("mutation_type"),
                    source="evolution",
                    arm=cand.arm,
                    campaign_task_id=cand.campaign_task_id,
                )
            except Exception:  # noqa: BLE001
                counts["invalid"] += 1
                continue
            if not res.created:
                counts["duplicate"] += 1
                continue
            counts["created"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pop-size", type=int, default=30)
    ap.add_argument("--seed-family", default=None, help="filter seed alphas to a family")
    ap.add_argument("--max-parents", type=int, default=16)
    ap.add_argument("--arm", choices=["exploit", "random_stratified", "plateau_fill"], default=None)
    ap.add_argument("--force", action="store_true", help="bypass diversity gate")
    ap.add_argument("--simulate", type=int, default=0)
    ap.add_argument("--region", default="USA")
    ap.add_argument("--universe", default="TOP3000")
    ap.add_argument("--delay", type=int, default=1)
    args = ap.parse_args()

    with session_scope() as db:
        query = (
            select(Alpha)
            .outerjoin(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
            .where(
                Alpha.status.in_([AlphaStatus.PASSED.value, AlphaStatus.SUBMITTED.value]),
                Alpha.region == args.region,
                Alpha.universe == args.universe,
                Alpha.delay == args.delay,
            )
            .order_by(AlphaMetric.sharpe.desc().nullslast())
            .limit(args.max_parents)
        )
        if args.seed_family:
            query = query.where(Alpha.family_key == args.seed_family)

        parent_alphas = list(db.execute(query).scalars().all())

    if not parent_alphas:
        print("No eligible parent alphas found. Run family simulations first.")
        return 1

    # Pre-flight Diversity Gate (User Directive 5)
    is_diverse, found_ops = check_seed_diversity(parent_alphas)
    if not is_diverse and not args.force:
        print("=" * 70)
        print("⚠️  EVOLUTION DIVERSITY GATE BLOCKED:")
        print(f"   Found only {len(found_ops)} operator families in parent pool: {sorted(found_ops)}")
        print(f"   Evolution requires at least {MIN_DIVERSITY_FAMILIES} distinct operator families to prevent")
        print("   monoculture loops. Run Stage 3 constructor sweeps (Task 2a/2b) across")
        print("   multiple operator families first, or pass --force to bypass.")
        print("=" * 70)
        return 1

    parent_ids = [p.id for p in parent_alphas]
    print(f"Seeding evolution from {len(parent_ids)} parents ({len(found_ops)} operator families: {sorted(found_ops)})")

    cfg = EvolutionConfig(population_size=args.pop_size)
    settings = AlphaSettings(region=args.region, universe=args.universe, delay=args.delay)

    with session_scope() as db:
        candidates = evolve_generation(
            db, parent_ids, config=cfg, base_settings=settings, arm=args.arm
        )
    print(f"Evolved {len(candidates)} child candidates.")
    if not candidates:
        print("No candidates generated.")
        return 1

    counts = save(candidates)
    print(f"saved: {counts}")

    if args.simulate:
        ids = pending_alpha_ids(limit=args.simulate, family_key=candidates[0].family_key if candidates else None)
        if ids:
            print(f"simulating {len(ids)} on BRAIN (3 concurrent) ...")
            result = run_batch(ids)
            print(f"simulation: {result.as_dict()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
