"""Study: Effective Trials N_eff & DSR Diagnostic Study (W4)."""

from __future__ import annotations

import argparse
from collections import defaultdict
import math
import sys
from typing import Any

import numpy as np
from sqlalchemy import select

from app.db.session import session_scope
from app.models.alphas import Alpha
from app.services.correlation import _date_map, compute_pairwise_correlation
from app.services.pnl_storage import get_pnl_store
from app.services.subperiod import compute_effective_trials


def run_study(min_points: int = 5) -> dict[str, Any]:
    store = get_pnl_store()
    with session_scope() as db:
        alphas = list(db.execute(select(Alpha)).scalars().all())

        by_family: dict[str, list[Alpha]] = defaultdict(list)
        for a in alphas:
            if a.family_key:
                by_family[a.family_key].append(a)

    print("================ N_EFF FAMILY DIAGNOSTIC STUDY ================")
    print(f"Total Families Identified:                      {len(by_family)}")

    family_results = []
    for fam, fam_alphas in by_family.items():
        if len(fam_alphas) < min_points:
            continue

        pnl_vectors = []
        for a in fam_alphas:
            pnl_data = store.load_pnl(a.id)
            if pnl_data:
                _, vals = pnl_data
                if len(vals) >= 20:
                    pnl_vectors.append(vals[:252])

        if len(pnl_vectors) < 2:
            continue

        min_len = min(len(v) for v in pnl_vectors)
        aligned = np.array([v[:min_len] for v in pnl_vectors])
        corr_matrix = np.corrcoef(aligned)
        corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)

        n_eff = compute_effective_trials(corr_matrix)
        family_results.append({
            "family_key": fam,
            "n_members": len(fam_alphas),
            "pnl_members": len(pnl_vectors),
            "n_eff": n_eff,
            "ratio": n_eff / max(1, len(pnl_vectors)),
        })

    print(f"Evaluated Families with PnL >= {min_points}:             {len(family_results)}")
    for r in sorted(family_results, key=lambda x: -x["n_members"])[:10]:
        print(f"  - {r['family_key']}: N={r['pnl_members']}, N_eff={r['n_eff']:.2f} (ratio {r['ratio']:.2f})")
    print("===============================================================")

    return {"families_evaluated": len(family_results), "results": family_results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-points", type=int, default=5, help="Minimum points per family")
    args = ap.parse_args()
    run_study(min_points=args.min_points)
    return 0


if __name__ == "__main__":
    sys.exit(main())
