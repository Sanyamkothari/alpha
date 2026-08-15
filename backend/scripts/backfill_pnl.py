"""Script to backfill daily PnL vectors for all simulated alphas in the database.

Usage:
    python -m scripts.backfill_pnl
"""

from __future__ import annotations

import argparse
import math
import sys
import numpy as np
import structlog
from sqlalchemy import select

from app.db import session_scope
from app.models.alphas import Alpha
from app.models.results import AlphaMetric, SimulationImport
from app.services.brain import BrainClient
from app.services.pnl_storage import get_pnl_store

log = structlog.get_logger("backfill_pnl")


def backfill_all(limit: int | None = None) -> dict[str, int]:
    store = get_pnl_store()
    stats = {"remote_fetched": 0, "matched": 0, "saved": 0, "reconciled": 0, "failed": 0, "skipped": 0}

    from app.services.subperiod import verify_pnl_reconciliation

    with session_scope() as db:
        db_alphas = db.query(Alpha).all()
        expr_to_alpha = {}
        for a in db_alphas:
            expr_to_alpha[(a.expression.strip(), a.neutralization, a.decay)] = a
            expr_to_alpha[a.expression.strip()] = a

    print(f"Connecting to BRAIN to fetch remote alphas and daily PnL series...")
    try:
        with BrainClient() as brain:
            remote_alphas = list(brain.iter_paginated("/users/self/alphas", page_size=50))
            stats["remote_fetched"] = len(remote_alphas)
            print(f"Retrieved {len(remote_alphas)} remote alphas from BRAIN.")

            if limit:
                remote_alphas = remote_alphas[:limit]

            for ra in remote_alphas:
                code = ra.get("regular", {}).get("code", "").strip()
                settings = ra.get("settings", {})
                neutr = settings.get("neutralization")
                decay = settings.get("decay", 0)

                local_alpha = expr_to_alpha.get((code, neutr, decay)) or expr_to_alpha.get(code)
                if not local_alpha:
                    continue

                stats["matched"] += 1
                if store.load_pnl(local_alpha.id) is not None:
                    stats["skipped"] += 1
                    continue

                r_id = ra.get("id")
                if not r_id:
                    stats["failed"] += 1
                    continue

                try:
                    pnl_resp = brain.get_json(f"/alphas/{r_id}/recordsets/daily-pnl")
                    records = pnl_resp.get("records", [])
                    if records:
                        dates = [str(r[0]) for r in records]
                        pnl = np.array([float(r[1]) for r in records], dtype=float)
                        store.save_pnl(local_alpha.id, dates, pnl)
                        stats["saved"] += 1

                        rep_sr = float((ra.get("is") or {}).get("sharpe", 0.0))
                        rec = verify_pnl_reconciliation(local_alpha.id, rep_sr, store, sharpe_tolerance=0.10)
                        if rec.is_valid:
                            stats["reconciled"] += 1
                    else:
                        stats["failed"] += 1
                except Exception as exc:
                    log.warning("pnl_fetch_failed", alpha_id=local_alpha.id, remote_id=r_id, error=str(exc))
                    stats["failed"] += 1
    except Exception as exc:
        print(f"BRAIN client unavailable: {exc}. No synthetic PnL will be written.")
        return stats

    print(f"Backfill complete: {stats}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    backfill_all(limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
