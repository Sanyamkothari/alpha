"""The loop: expand a family, save it, simulate it, report the plateau.

    python -m scripts.run_family --field liabilities --denominator cap
    python -m scripts.run_family --field liabilities --denominator cap --simulate 48
    python -m scripts.run_family --field assets --denominator cap --simulate 0   # expand only

``--simulate N`` caps how many of the emitted candidates actually go to BRAIN.
At ~90 s each across 3 slots, 48 candidates is roughly 25 minutes — start small,
confirm the surface looks sane, then widen.

**The cap slices the emitted order, and the emitted order is surface-contiguous.**
``--simulate 27`` on a 3x3 grid therefore gets you three complete surfaces, not 27
points scattered across nine — which is what the plateau filter needs to read.

Screening a settings axis (STRATEGY.md Rule 2 / plan item B2)::

    # Stage A - probe 3 truncation levels on the coarse 3x3 grid (27 sims)
    python -m scripts.run_family --field liabilities --denominator cap \
        --grid coarse --truncations 0.08 0.04 0.01 --settings-per-structure 3 \
        --operators ts_zscore --wrappers rank --simulate 27

    # Stage B - promote the winning level to a full 7x7 surface (49 sims)
    python -m scripts.run_family --field liabilities --denominator cap \
        --truncations 0.01 --operators ts_zscore --wrappers rank --simulate 49

76 simulations to answer the question, against 147 for a full 3 x 7x7 sweep.
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
                    arm=cand.arm,
                    campaign_task_id=cand.campaign_task_id,
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


from scripts._cli import cli_main


@cli_main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--field", required=True)
    ap.add_argument("--denominator", default=None)
    ap.add_argument("--mechanism", default="")
    ap.add_argument(
        "--grid",
        choices=["standard", "wide", "coarse"],
        default="standard",
        help="standard (7x7=49), wide (6x4=24), or coarse (3x3=9, for settings probes)",
    )
    ap.add_argument("--operators", nargs="*", help="ts operator families to sweep (e.g. ts_zscore ts_rank)")
    ap.add_argument("--wrappers", nargs="*", help="cross-section wrappers (rank zscore normalize none)")
    ap.add_argument("--groups", nargs="*", help="group fields (sector industry subindustry none)")
    ap.add_argument("--windows", nargs="*", type=int, help="custom lookback windows")
    ap.add_argument("--decays", nargs="*", type=int, help="custom decays")
    ap.add_argument(
        "--truncations",
        nargs="*",
        type=float,
        help="truncation levels to sweep, reference level first (e.g. 0.08 0.04 0.01)",
    )
    ap.add_argument(
        "--neutralizations", nargs="*", help="neutralizations to sweep (SUBINDUSTRY INDUSTRY ...)"
    )
    ap.add_argument(
        "--settings-per-structure",
        type=int,
        default=1,
        help="how many settings combinations each structure gets. 1 (default) emits only "
             "the reference combination; raise it to screen a settings axis",
    )
    ap.add_argument("--arm", choices=["exploit", "random_stratified", "plateau_fill"], default=None)
    ap.add_argument("--max-candidates", type=int, default=None)
    ap.add_argument("--simulate", type=int, default=0, help="how many to actually run on BRAIN")
    ap.add_argument("--no-backfill", action="store_true")
    ap.add_argument("--region", default="USA")
    ap.add_argument("--universe", default="TOP3000")
    ap.add_argument("--delay", type=int, default=1)
    args = ap.parse_args()

    from app.services.constructor import (
        BudgetPolicy,
        COARSE_DECAYS,
        COARSE_WINDOWS,
        DEFAULT_CROSS_SECTION,
        DEFAULT_GROUPS,
        DEFAULT_NEUTRALIZATIONS,
        DEFAULT_TRUNCATIONS,
        DEFAULT_TS_TRANSFORMS,
        GridAxes,
        STANDARD_DECAYS,
        STANDARD_WINDOWS,
        WIDE_DECAYS,
        WIDE_WINDOWS,
    )

    grid_defaults = {
        "standard": (STANDARD_WINDOWS, STANDARD_DECAYS),
        "wide": (WIDE_WINDOWS, WIDE_DECAYS),
        "coarse": (COARSE_WINDOWS, COARSE_DECAYS),
    }
    default_windows, default_decays = grid_defaults[args.grid]
    windows = tuple(args.windows) if args.windows else default_windows
    decays = tuple(args.decays) if args.decays else default_decays

    # "none" is how you say None on a command line.
    def _optional(values: list[str] | None, fallback: tuple) -> tuple:
        if not values:
            return fallback
        return tuple(None if v.lower() == "none" else v for v in values)

    axes = GridAxes(
        ts_transforms=tuple(args.operators) if args.operators else DEFAULT_TS_TRANSFORMS,
        cross_section=_optional(args.wrappers, DEFAULT_CROSS_SECTION),
        groups=_optional(args.groups, DEFAULT_GROUPS),
        windows=windows,
        decays=decays,
        truncations=tuple(args.truncations) if args.truncations else DEFAULT_TRUNCATIONS,
        neutralizations=tuple(args.neutralizations) if args.neutralizations else DEFAULT_NEUTRALIZATIONS,
    )

    # Pinning the structure matters for a settings probe, and the reason is not
    # obvious: select_surface_configs deliberately spends the budget on *structural*
    # diversity, so an unpinned run asking for 3 surfaces returns 3 different
    # structures at arbitrary settings — the opposite of what a probe needs, which is
    # one structure at 3 settings. Naming exactly one operator (and optionally one
    # wrapper) sets operator_family/wrapper_shape, which also suppresses the depth-2
    # and ts_corr layers, so the only axis left varying is the settings axis.
    pinned_operator = args.operators[0] if args.operators and len(args.operators) == 1 else None
    pinned_wrapper = None
    if args.wrappers and len(args.wrappers) == 1 and args.wrappers[0].lower() != "none":
        pinned_wrapper = args.wrappers[0]

    settings = AlphaSettings(region=args.region, universe=args.universe, delay=args.delay)
    spec = FamilySpec(
        field_code=args.field,
        denominator=args.denominator,
        mechanism=args.mechanism,
        operator_family=pinned_operator,
        wrapper_shape=pinned_wrapper,
        grid_mode=args.grid if args.grid in ("standard", "wide") else "standard",
        axes=axes,
    )
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
            print("  (the catalog may still be readable — that does not imply access)")
            return 1

    surface_size = len(windows) * len(decays)
    # Default to one surface per settings combination being screened, so
    # --settings-per-structure 3 on the coarse grid asks for 27 and gets 27.
    settings_per_structure = max(1, args.settings_per_structure)
    max_cands = args.max_candidates or surface_size * settings_per_structure

    with session_scope() as db:
        candidates = expand(
            db,
            spec,
            base_settings=settings,
            max_candidates=max_cands,
            arm=args.arm,
            policy=BudgetPolicy(
                max_surfaces=max(1, max_cands // surface_size),
                settings_per_structure=settings_per_structure,
            ),
        )
    print(
        f"expanded: {len(candidates)} candidates for family {family_key!r} "
        f"(grid={args.grid} {len(windows)}x{len(decays)}, "
        f"settings/structure={settings_per_structure}, arm={args.arm})"
    )
    trunc_levels = sorted({c.settings.truncation for c in candidates})
    if len(trunc_levels) > 1:
        print(f"  truncation levels emitted: {trunc_levels}")
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
