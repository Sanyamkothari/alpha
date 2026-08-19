"""Measure the filter stack against the alphas BRAIN has already accepted.

`scripts/calibrate_filter.py` scores the filter on synthetic ground truth: it knows
which families carry true signal because it generated them. This script asks the
complementary question, and the one with real stakes:

    How many of the alphas already in the live submitted portfolio would this filter
    promote today?

A filter that rejects what the platform accepted is mis-tuned no matter how clean the
synthetic scorecard looks. Every alpha in `submitted_portfolio()` is a known positive
— it cleared BRAIN's own checks and a human chose to submit it — so the promotion rate
over that set is a lower bound on the filter's recall on real data.

Usage:
    python -m scripts.calibrate_against_portfolio
    python -m scripts.calibrate_against_portfolio --n-eff 1 3 5 8 12 20 49

Requires the local database and backfilled PnL (`python -m scripts.backfill_pnl`).
Alphas with no stored PnL are reported separately rather than counted as failures —
absent evidence is not evidence of a bad alpha.
"""

from __future__ import annotations

import argparse

import numpy as np

from app.db import session_scope
from app.models.results import AlphaMetric
from app.services.correlation import submitted_portfolio
from app.services.filter_config import DEFAULT_FILTER_CONFIG, TRADING_DAYS_PER_YEAR
from app.services.plateau import haircut_bar
from app.services.pnl_storage import get_pnl_store
from app.services.subperiod import evaluate_subperiod_stability
from scripts._cli import cli_main

DEFAULT_N_EFF = (1, 3, 5, 8, 12, 20, 49)


def _reported_sharpe(db, alpha_id: int) -> float | None:
    """Latest reported Sharpe for an alpha, or None if it was never simulated."""
    from sqlalchemy import select

    row = db.execute(
        select(AlphaMetric.sharpe)
        .where(AlphaMetric.alpha_id == alpha_id)
        .order_by(AlphaMetric.id.desc())
        .limit(1)
    ).scalar()
    return float(row) if row is not None else None


@cli_main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--n-eff",
        type=float,
        nargs="+",
        default=list(DEFAULT_N_EFF),
        help="Effective trial counts to price the haircut bar at.",
    )
    args = ap.parse_args()
    cfg = DEFAULT_FILTER_CONFIG

    with session_scope() as db:
        portfolio = submitted_portfolio(db)
        if not portfolio:
            print(
                "No submitted alphas found. The portfolio is defined by submission\n"
                "attempts (result='submitted', not recalled) — record them with\n"
                "`python -m scripts.record_past_attempts` or mark them in the console."
            )
            return 1

        store = get_pnl_store()
        print(f"Filter fingerprint : {cfg.fingerprint()}")
        print(f"Submitted alphas   : {len(portfolio)}")
        print(f"PnL directory      : {store._dir}\n")

        rows = []
        for alpha in portfolio:
            reported = _reported_sharpe(db, alpha.id)
            loaded = store.load_pnl(alpha.id)

            realised = None
            subperiod_ok = None
            sub_reasons: list[str] = []
            if loaded is not None:
                _, pnl = loaded
                arr = np.asarray(pnl, dtype=np.float64)
                sd = float(np.std(arr, ddof=1))
                if sd > 0:
                    realised = float(np.mean(arr) / sd * np.sqrt(TRADING_DAYS_PER_YEAR))
                verdict = evaluate_subperiod_stability(arr, cfg=cfg)
                subperiod_ok = verdict.passed
                sub_reasons = list(verdict.reasons)

            rows.append(
                {
                    "id": alpha.id,
                    "expr": (alpha.expression or "")[:46],
                    "reported": reported,
                    "realised": realised,
                    "subperiod": subperiod_ok,
                    "reasons": sub_reasons,
                }
            )

        # ---- per-alpha detail -------------------------------------------------
        print(f"{'id':>7}  {'reported':>8}  {'realised':>8}  {'sub-period':>10}  expression")
        for r in rows:
            rep = f"{r['reported']:.2f}" if r["reported"] is not None else "—"
            rea = f"{r['realised']:.2f}" if r["realised"] is not None else "no PnL"
            sub = "—" if r["subperiod"] is None else ("pass" if r["subperiod"] else "FAIL")
            print(f"{r['id']:>7}  {rep:>8}  {rea:>8}  {sub:>10}  {r['expr']}")

        measured = [r for r in rows if r["reported"] is not None]
        no_metric = len(rows) - len(measured)
        with_pnl = [r for r in rows if r["realised"] is not None]

        # ---- the haircut bar, priced across plausible effective trial counts ----
        print(
            f"\nHaircut bar vs the {len(measured)} submitted alphas that carry a reported Sharpe:"
        )
        print(f"{'n_eff':>6}  {'bar':>6}  {'clears':>8}   rejected (known-accepted alphas)")
        for n_eff in args.n_eff:
            bar = haircut_bar(n_eff, cfg=cfg)
            below = [r for r in measured if (r["reported"] or 0.0) < bar]
            names = ", ".join(f"#{r['id']}" for r in below[:6])
            if len(below) > 6:
                names += f" (+{len(below) - 6})"
            print(
                f"{n_eff:>6.0f}  {bar:>6.2f}  {len(measured) - len(below):>4}/{len(measured):<3}"
                f"   {names or '—'}"
            )

        # ---- summary ----------------------------------------------------------
        if measured:
            sharpes = sorted(r["reported"] for r in measured)
            mid = len(sharpes) // 2
            median = (
                sharpes[mid] if len(sharpes) % 2 else (sharpes[mid - 1] + sharpes[mid]) / 2
            )
            print(
                f"\nAccepted-portfolio Sharpe: min {sharpes[0]:.2f}  "
                f"median {median:.2f}  max {sharpes[-1]:.2f}"
            )
            print(
                "Read the table as recall: every row above is an alpha BRAIN accepted, so\n"
                "an n_eff whose bar rejects them is an n_eff at which this filter would\n"
                "have talked you out of your own portfolio."
            )
        if with_pnl:
            failed = [r for r in with_pnl if r["subperiod"] is False]
            print(
                f"\nSub-period stability: {len(with_pnl) - len(failed)}/{len(with_pnl)} "
                f"of the alphas with stored PnL pass."
            )
            for r in failed:
                print(f"  #{r['id']}: {'; '.join(r['reasons'][:2])}")
        if no_metric:
            print(f"\n{no_metric} submitted alpha(s) have no stored metrics — not scored.")
        missing_pnl = len(rows) - len(with_pnl)
        if missing_pnl:
            print(
                f"{missing_pnl} submitted alpha(s) have no stored PnL — run "
                "`python -m scripts.backfill_pnl` for a complete picture."
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
