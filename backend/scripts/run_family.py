"""The loop: expand a family, save it, simulate it, report the plateau.

    python -m scripts.run_family --field liabilities --denominator cap
    python -m scripts.run_family --field liabilities --denominator cap --simulate 48
    python -m scripts.run_family --field assets --denominator cap --simulate 0   # expand only

``--simulate N`` caps how many of the emitted candidates actually go to BRAIN.
At ~90 s each across 3 slots, 48 candidates is roughly 25 minutes — start small,
confirm the surface looks sane, then widen.
"""

from __future__ import annotations

import argparse
import sys

import structlog

from app.db import session_scope
from app.models.alphas import Alpha
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import Candidate, FamilySpec, expand
from app.services.simulation_runner import pending_alpha_ids, run_batch

log = structlog.get_logger("run_family")


def save(candidates: list[Candidate]) -> dict[str, int]:
    """Persist candidates as untested alphas. Existing hashes are skipped."""
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
                    source="constructor",
                )
            except Exception:  # noqa: BLE001 - validator gate; count and continue
                counts["invalid"] += 1
                continue
            if not res.created:
                counts["duplicate"] += 1
                continue
            # Keep the grid coordinates on the row: the plateau filter needs to
            # know which point of the surface this alpha is.
            counts["created"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--field", required=True)
    ap.add_argument("--denominator", default=None)
    ap.add_argument("--mechanism", default="")
    ap.add_argument("--max-candidates", type=int, default=384)
    ap.add_argument("--simulate", type=int, default=0, help="how many to actually run on BRAIN")
    ap.add_argument("--no-backfill", action="store_true")
    ap.add_argument("--region", default="USA")
    ap.add_argument("--universe", default="TOP3000")
    ap.add_argument("--delay", type=int, default=1)
    args = ap.parse_args()

    spec = FamilySpec(
        field_code=args.field,
        denominator=args.denominator,
        mechanism=args.mechanism or f"{args.field} signal",
        backfill_days=None if args.no_backfill else 120,
    )

    settings = AlphaSettings(region=args.region, universe=args.universe, delay=args.delay)
    family_key = spec.family_key(settings)

    # Preflight before generating anything. A config can be fully *readable* and
    # still not simulatable: delay-0 exposes 2,121 fields via GET but rejects the
    # simulation with "Delay 0 is not available." Finding that out after
    # expansion means hundreds of dead alpha rows.
    if args.simulate:
        from app.services.brain import BrainClient, SimulationSettings

        with BrainClient() as brain:
            ok, why = brain.config_available(
                SimulationSettings(region=args.region, universe=args.universe, delay=args.delay)
            )
        if not ok:
            print(f"config {args.region}/{args.universe}/delay{args.delay} is NOT simulatable")
            print(f"  {why}")
            print("  (the catalog may still be readable — that does not imply access)")
            return 1
    with session_scope() as db:
        candidates = expand(db, spec, base_settings=settings, max_candidates=args.max_candidates)
    print(f"expanded: {len(candidates)} candidates for family {family_key!r}")
    if not candidates:
        print("nothing emitted — check the field code exists for this region/delay/universe")
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
    print(f"family {family_key!r} now holds {total} alphas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
