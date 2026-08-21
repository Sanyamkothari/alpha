"""Verify and reconcile loaded daily PnL vectors against reported BRAIN metrics.

Empirically tests whether /alphas/{id}/recordsets/daily-pnl returns discrete
daily dollar PnL series (non-cumulative) vs cumulative PnL curves, and measures
annualization constant reconciliation (sqrt(250) vs sqrt(252)).

Usage:
    python -m scripts.verify_pnl_reconciliation
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
from scipy import stats
from sqlalchemy import select

from app.db import session_scope
from app.models.results import AlphaMetric
from app.services.pnl_storage import get_pnl_store
from app.services.subperiod import verify_pnl_reconciliation


def run() -> dict[str, float | int]:
    store = get_pnl_store()
    npy_files = list(store._dir.glob("*.npy"))
    alpha_ids = [int(f.stem) for f in npy_files if f.stem.isdigit()]

    if not alpha_ids:
        print("No stored .npy PnL vectors found in database/pnl/")
        return {"total_pnl_files": 0, "reconciled": 0, "pass_rate": 0.0}

    reported = []
    recomputed = []
    diffs = []
    valid_count = 0

    with session_scope() as db:
        for aid in sorted(alpha_ids):
            metric = (
                db.execute(select(AlphaMetric).where(AlphaMetric.alpha_id == aid)).scalars().first()
            )
            if metric and metric.sharpe is not None:
                r = verify_pnl_reconciliation(aid, metric.sharpe, store)
                reported.append(metric.sharpe)
                recomputed.append(r.recomputed_sharpe)
                diffs.append(r.sharpe_diff)
                if r.is_valid:
                    valid_count += 1

    rep_arr = np.array(reported)
    rec_arr = np.array(recomputed)
    diff_arr = np.array(diffs)

    slope, intercept, r_val, p_val, std_err = stats.linregress(rep_arr, rec_arr)
    annual_diff = (np.sqrt(252 / 250) - 1.0) * np.abs(rep_arr)
    unexplained = np.abs(diff_arr - annual_diff)

    print(f"Total stored PnL files: {len(alpha_ids)}")
    print(f"Reconciled with AlphaMetric: {len(reported)}")
    print(
        f"Passing tolerance (diff <= 0.05): {valid_count}/{len(reported)} ({valid_count/len(reported)*100:.1f}%)"
    )
    print(
        f"Sharpe diff distribution: min={np.min(diff_arr):.4f}, median={np.median(diff_arr):.4f}, mean={np.mean(diff_arr):.4f}, max={np.max(diff_arr):.4f}"
    )
    print("Regression of recomputed Sharpe on reported Sharpe:")
    print(f"  Slope: {slope:.6f} (theoretical sqrt(252/250) = {np.sqrt(252/250):.6f})")
    print(f"  Intercept: {intercept:.6f}")
    print(f"  R^2: {r_val**2:.6f}")
    print(
        f"  Unexplained residual after sqrt(252/250) correction: mean={np.mean(unexplained):.6f}, max={np.max(unexplained):.6f}"
    )

    return {
        "total_pnl_files": len(alpha_ids),
        "reconciled": len(reported),
        "valid_count": valid_count,
        "pass_rate": valid_count / len(reported) if reported else 0.0,
        "slope": float(slope),
        "r_squared": float(r_val**2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
