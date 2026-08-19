"""Audit the existing PnL store for daily vs cumulative series and Sharpe reconciliation.

Usage:
    python -m scripts.audit_pnl
"""

from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import structlog

from app.db.session import SessionLocal
from app.models.alphas import Alpha
from app.models.results import AlphaMetric
from app.services.filter_config import DEFAULT_FILTER_CONFIG, TRADING_DAYS_PER_YEAR
from app.services.pnl_storage import (
    PnLStore,
    compute_lag1_autocorr,
    compute_vector_sharpe,
    get_pnl_store,
    is_cumulative_series,
)
from app.services.subperiod import verify_pnl_reconciliation

log = structlog.get_logger("audit_pnl")


def audit_store(store: PnLStore | None = None) -> int:
    store = store or get_pnl_store()
    pnl_dir = store._dir
    npy_files = sorted(pnl_dir.glob("*.npy"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)

    print(f"Auditing {len(npy_files)} PnL vectors in {pnl_dir}...")
    if not npy_files:
        print("No PnL files found.")
        return 0

    db = SessionLocal()
    total = len(npy_files)
    reconciled_count = 0
    cumulative_detected = 0
    unreconciled_list = []

    for f in npy_files:
        alpha_id = int(f.stem)
        res = store.load_pnl(alpha_id)
        if res is None:
            continue
        dates, arr = res
        recomputed_sr = compute_vector_sharpe(arr)
        frac_pos = float((arr > 0).mean())
        autocorr = compute_lag1_autocorr(arr)
        is_cum = is_cumulative_series(arr)

        if is_cum:
            cumulative_detected += 1

        metric = db.query(AlphaMetric).filter(AlphaMetric.alpha_id == alpha_id).first()
        reported_sr = metric.sharpe if metric and metric.sharpe is not None else None

        if reported_sr is not None:
            rec = verify_pnl_reconciliation(
                alpha_id, reported_sr, store, sharpe_tolerance=DEFAULT_FILTER_CONFIG.sharpe_reconciliation_tolerance
            )
            if rec.is_valid:
                reconciled_count += 1
            else:
                unreconciled_list.append((alpha_id, reported_sr, recomputed_sr, rec.error))
        else:
            # Without reported Sharpe, treat as non-reconciled
            unreconciled_list.append((alpha_id, None, recomputed_sr, "No reported Sharpe in database"))

    db.close()

    reconciled_pct = (reconciled_count / total * 100) if total else 0.0
    print("\n--- PnL Audit Report ---")
    print(f"Total Vectors Audited: {total}")
    print(f"Reconciled: {reconciled_count} ({reconciled_pct:.1f}%)")
    print(f"Cumulative Series Detected: {cumulative_detected}")
    print(f"Unreconciled / Missing Metric: {len(unreconciled_list)}")

    if unreconciled_list[:5]:
        print("\nSample Unreconciled Vectors:")
        for aid, rep, recomp, err in unreconciled_list[:5]:
            print(f"  Alpha #{aid}: reported={rep}, recomputed={recomp:.3f} | {err}")

    if cumulative_detected > 0:
        print("\n[FAIL] Cumulative series detected in PnL store!")
        return 1

    print("\n[SUCCESS] PnL vectors are daily increments.")
    return 0


if __name__ == "__main__":
    sys.exit(audit_store())
