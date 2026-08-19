"""Answer "will this setting do anything?" from stored metrics, before spending sims.

BRAIN's fitness is a published closed form::

    fitness = Sharpe * sqrt(|returns| / max(turnover, 0.125))

which means every candidate fix can be priced arithmetically *before* it is
simulated. This script does two things for a family:

1. **Is truncation slack?** Truncation caps the weight of any single name. A
   ``rank()`` alpha spreads weight almost evenly, so its largest position is about
   ``2/N`` of the book for N held names. Truncation therefore does nothing at all
   unless ``2/N`` exceeds it — with 1,000 names the biggest position is 0.2% and a
   1% cap never binds. ``long_count + short_count`` settles it.

2. **What would actually clear the bars?** Given a point's Sharpe and returns, it
   solves for the turnover that would put fitness over the floor, so you can see
   whether the gap is reachable at all.

    python -m scripts.diagnose_settings --family "liabilities/cap@USA/TOP3000/d1"
    python -m scripts.diagnose_settings --all
"""

from __future__ import annotations

import argparse
import math

from sqlalchemy import select

from app.db.session import session_scope
from app.models.alphas import Alpha
from app.models.results import AlphaMetric

# BRAIN applies eight checks. Only these three can be rebuilt from stored scalars.
# The other five - CONCENTRATED_WEIGHT, LOW_SUB_UNIVERSE_SHARPE, MATCHES_COMPETITION,
# LOW_TURNOVER's own edge cases, SELF_CORRELATION - are invisible to any reconstruction.
# That gap is not academic: an independent audit found a family this tool reported as
# 9/16 clearing was 0/16 on BRAIN's own verdict, every point failing
# LOW_SUB_UNIVERSE_SHARPE. Reconstruction OVERSTATES. Always prefer passed_all_checks.
RECONSTRUCTABLE_CHECKS = ("LOW_SHARPE", "LOW_FITNESS", "HIGH_TURNOVER", "LOW_TURNOVER")

TURNOVER_FLOOR = 0.125  # the max() floor inside the fitness formula
FITNESS_BAR = 1.0
TURNOVER_CEILING = 0.70   # HIGH_TURNOVER
TURNOVER_MIN = 0.01       # LOW_TURNOVER
SHARPE_BAR = 1.25         # LOW_SHARPE

TRUNCATION_LEVELS = (0.08, 0.04, 0.01)


def _failed_check_names(metric) -> list[str]:
    """Names of the BRAIN checks this alpha actually failed, from the stored payload."""
    out = []
    for entry in metric.checks or []:
        if isinstance(entry, dict) and str(entry.get("result", "")).upper() == "FAIL":
            name = entry.get("name")
            if name:
                limit, value = entry.get("limit"), entry.get("value")
                out.append(
                    f"{name}({value}<{limit})" if limit is not None and value is not None else str(name)
                )
    return out


def fitness_of(sharpe: float, returns: float, turnover: float) -> float:
    return sharpe * math.sqrt(abs(returns) / max(turnover, TURNOVER_FLOOR))


def required_turnover(sharpe: float, returns: float, bar: float = FITNESS_BAR) -> float | None:
    """The turnover at which fitness would reach `bar`, holding Sharpe and returns.

    Returns None when the bar is unreachable by cutting turnover alone. That happens
    because of the ``max(turnover, 0.125)`` floor: once turnover is at or below
    0.125 the denominator stops shrinking, so further reductions do nothing for
    fitness — and in practice they also drag returns down, which makes it worse.
    Without this guard the advice reads "cut turnover to 0.11" for an alpha already
    at 0.07, which is not achievable and not useful.
    """
    if sharpe <= 0:
        return None
    needed = abs(returns) / ((bar / sharpe) ** 2)
    if needed < TURNOVER_FLOOR:
        # Even at the floor the bar is not met; turnover is not the lever here.
        return None
    return needed


def report_family(family_key: str) -> None:
    with session_scope() as db:
        rows = db.execute(
            select(Alpha, AlphaMetric)
            .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
            .where(Alpha.family_key == family_key)
        ).all()

    if not rows:
        print(f"no simulated alphas in family {family_key!r}")
        return

    print(f"\n=== {family_key} — {len(rows)} simulated points ===\n")

    # ---- 1. does truncation bind? ----
    counts = [
        (m.long_count or 0) + (m.short_count or 0)
        for _, m in rows
        if m.long_count is not None or m.short_count is not None
    ]
    if not counts:
        print("long_count/short_count not stored for this family — cannot judge truncation.")
    elif max(counts) == 0:
        print(
            "every point held ZERO positions — these simulations produced no book at all.\n"
            "  The field is almost certainly empty for this region/delay/universe.\n"
            "  Truncation is meaningless here; the family should be retired, not tuned."
        )
    else:
        smallest = min(c for c in counts if c > 0)
        typical = sorted(counts)[len(counts) // 2]
        print(f"positions held: median {typical}, smallest {smallest}")
        max_weight = 2.0 / smallest if smallest else float("inf")
        print(f"largest position of a rank() alpha ~ 2/N = {max_weight:.2%} of book\n")
        for level in TRUNCATION_LEVELS:
            binds = max_weight > level
            verdict = "BINDS — changing it will move the book" if binds else "slack — no effect"
            print(f"  truncation {level:<5} vs max weight {max_weight:.2%}   {verdict}")
        if max_weight <= min(TRUNCATION_LEVELS):
            print(
                "\n  => Truncation is slack at every level tested. Sweeping it will return a\n"
                "     flat line. Spend the simulations on an axis that binds instead."
            )

    # ---- 2. what would clear the bars? ----
    print("\nfitness = Sharpe * sqrt(|returns| / max(turnover, 0.125))")
    print("bars shown: Sharpe >= 1.25, fitness >= 1.00, 0.01 <= turnover <= 0.70")
    print("WARNING: those are 4 of BRAIN's 8 checks. Where passed_all_checks is stored it")
    print("         overrides them — reconstruction cannot see LOW_SUB_UNIVERSE_SHARPE or")
    print("         CONCENTRATED_WEIGHT and systematically overstates how many alphas pass.")
    print("note: the max(.,0.125) floor means turnover below 0.125 buys no fitness at all\n")
    print(f"  {'decay':>6} {'Sharpe':>7} {'return':>7} {'turnov':>7} {'fitness':>8}  what it needs")
    for alpha, m in sorted(rows, key=lambda r: (r[0].decay or 0)):
        if m.sharpe is None or m.returns is None or m.turnover is None:
            continue
        need = required_turnover(m.sharpe, m.returns)
        # All three bars, not just fitness. An earlier version reported PASSES for
        # points with Sharpe below the 1.25 LOW_SHARPE floor.
        blockers = []
        if m.sharpe < SHARPE_BAR:
            blockers.append(f"Sharpe {m.sharpe:.2f}<{SHARPE_BAR}")
        if (m.fitness or 0) < FITNESS_BAR:
            blockers.append(f"fitness {m.fitness or 0:.2f}<{FITNESS_BAR}")
        if m.turnover > TURNOVER_CEILING:
            blockers.append(f"turnover {m.turnover:.2f}>{TURNOVER_CEILING}")
        if m.turnover < TURNOVER_MIN:
            blockers.append(f"turnover {m.turnover:.2f}<{TURNOVER_MIN}")

        # BRAIN's own verdict wins whenever we have it. The reconstruction below sees
        # four of eight checks and will happily call an alpha passing that BRAIN failed.
        if m.passed_all_checks is False:
            failed = _failed_check_names(m)
            extra = [c for c in failed if c not in RECONSTRUCTABLE_CHECKS]
            if extra:
                verdict = "BRAIN says FAIL: " + ", ".join(extra)
            else:
                verdict = "BRAIN says FAIL: " + (", ".join(failed) or "; ".join(blockers))
        elif m.passed_all_checks is True:
            verdict = "PASSES (BRAIN verdict)"
        elif not blockers:
            verdict = "passes the 4 reconstructable checks — BRAIN verdict NOT stored"
        elif blockers == [f"fitness {m.fitness or 0:.2f}<{FITNESS_BAR}"] and need is not None:
            verdict = f"cut turnover to <={min(need, TURNOVER_CEILING):.2f} (now {m.turnover:.2f})"
        elif need is None and (m.fitness or 0) < FITNESS_BAR and m.turnover <= TURNOVER_FLOOR:
            verdict = "already at the 0.125 floor — needs Sharpe or returns, not less turnover"
        else:
            verdict = "; ".join(blockers)
        print(
            f"  {alpha.decay or 0:>6} {m.sharpe:>7.2f} {m.returns:>7.1%} "
            f"{m.turnover:>7.2f} {m.fitness or 0:>8.2f}  {verdict}"
        )
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family", help="family key to diagnose")
    ap.add_argument("--all", action="store_true", help="every family with simulated points")
    args = ap.parse_args()

    if args.all:
        with session_scope() as db:
            families = [
                f for (f,) in db.execute(
                    select(Alpha.family_key)
                    .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
                    .where(Alpha.family_key.is_not(None))
                    .group_by(Alpha.family_key)
                ).all()
            ]
        for fam in families:
            report_family(fam)
        return 0

    if not args.family:
        ap.error("pass --family or --all")
    report_family(args.family)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
