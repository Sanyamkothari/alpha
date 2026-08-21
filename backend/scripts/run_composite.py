"""Run multi-factor composite families (Phase 1).

python -m scripts.run_composite --primary liabilities --secondary cap --mode blend
python -m scripts.run_composite --primary liabilities --secondary assets --mode spread --simulate 48
"""

from __future__ import annotations

import argparse
import sys

import structlog

from app.db import session_scope
from app.models.alphas import Alpha
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.composite_constructor import CompositeSpec, expand_composite
from app.services.constructor import Candidate
from app.services.simulation_runner import pending_alpha_ids, run_batch

log = structlog.get_logger("run_composite")


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
                    source="composite_constructor",
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
    ap.add_argument("--primary", required=True, help="primary field code")
    ap.add_argument("--secondary", required=True, help="secondary field code")
    ap.add_argument(
        "--mode",
        choices=["blend", "spread", "orthogonal", "conditional"],
        default="blend",
        help="composite mode",
    )
    ap.add_argument("--trigger", default=None, help="trigger field for conditional mode")
    ap.add_argument("--primary-denom", default=None)
    ap.add_argument("--secondary-denom", default=None)
    ap.add_argument("--mechanism", default="")
    ap.add_argument("--weight", type=float, default=0.5)
    ap.add_argument("--arm", choices=["exploit", "random_stratified", "plateau_fill"], default=None)
    ap.add_argument("--max-candidates", type=int, default=49)
    ap.add_argument("--simulate", type=int, default=0)
    ap.add_argument("--region", default="USA")
    ap.add_argument("--universe", default="TOP3000")
    ap.add_argument("--delay", type=int, default=1)
    args = ap.parse_args()

    spec = CompositeSpec(
        primary_field=args.primary,
        secondary_field=args.secondary,
        mode=args.mode,
        trigger_field=args.trigger,
        primary_denominator=args.primary_denom,
        secondary_denominator=args.secondary_denom,
        mechanism=args.mechanism or f"{args.mode} composite: {args.primary}+{args.secondary}",
        weight=args.weight,
    )

    settings = AlphaSettings(region=args.region, universe=args.universe, delay=args.delay)
    family_key = spec.family_key(settings)

    if args.simulate:
        from app.services.brain import BrainClient, SimulationSettings

        with BrainClient() as brain:
            ok, why = brain.config_available(
                SimulationSettings(region=args.region, universe=args.universe, delay=args.delay)
            )
        if not ok:
            print(f"config {args.region}/{args.universe}/delay{args.delay} is NOT simulatable")
            print(f"  {why}")
            return 1

    with session_scope() as db:
        candidates = expand_composite(
            db, spec, base_settings=settings, max_candidates=args.max_candidates, arm=args.arm
        )
    print(f"expanded: {len(candidates)} candidates for composite {family_key!r} (arm={args.arm})")
    if not candidates:
        print("nothing emitted — check field codes exist for this market config")
        return 1

    counts = save(candidates)
    print(f"saved: {counts}")

    if args.simulate:
        ids = pending_alpha_ids(limit=args.simulate, family_key=family_key)
        print(f"simulating {len(ids)} on BRAIN (3 concurrent) ...")
        result = run_batch(ids)
        print(f"simulation: {result.as_dict()}")

    with session_scope() as db:
        total = db.query(Alpha).filter(Alpha.family_key == family_key).count()
    print(f"composite family {family_key!r} now holds {total} alphas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
