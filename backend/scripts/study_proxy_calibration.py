"""Study: Proxy Calibration & AST Degeneracy Diagnostic (W6 & W7)."""

from __future__ import annotations

import argparse
import sys
from typing import Any

import numpy as np
from sqlalchemy import select

from app.db.session import session_scope
from app.models.alphas import Alpha
from app.models.results import AlphaMetric
from app.services.proxy_calibration import (
    _spearman_rank_correlation,
    calibrate_proxy_rankings,
    is_degenerate_signal,
)
from app.validator.parser import parse


def run_study(sample_size: int = 500) -> dict[str, Any]:
    with session_scope() as db:
        report = calibrate_proxy_rankings(db, sample_size=sample_size)
        alphas = list(db.execute(select(Alpha)).scalars().all())

        total_alphas = len(alphas)
        degenerate_count = 0
        degenerate_reasons: dict[str, int] = {}

        for a in alphas:
            try:
                ast = parse(a.expression)
                is_deg, reason = is_degenerate_signal(ast)
                if is_deg:
                    degenerate_count += 1
                    degenerate_reasons[reason or "unknown"] = degenerate_reasons.get(reason or "unknown", 0) + 1
            except Exception:
                pass

    print("================ PROXY CALIBRATION REPORT ================")
    print(f"Sample Size:                                    {report.sample_size}")
    print(f"Turnover Proxy vs Actual Turnover Spearman Rho: {report.turnover_proxy_correlation:.4f}")
    print(f"Complexity vs Fitness Spearman Rho:             {report.complexity_vs_fitness_correlation:.4f}")
    print(f"Proxy System Viable (Rho > 0.40):               {report.is_proxy_viable}")
    print("
================ AST DEGENERACY AUDIT ====================")
    print(f"Total Alphas Audited:                           {total_alphas}")
    pct = (degenerate_count / max(1, total_alphas)) * 100
    print(f"Degenerate Alphas Detected:                     {degenerate_count} ({pct:.2f}%)")
    for reason, count in sorted(degenerate_reasons.items(), key=lambda x: -x[1]):
        print(f"  - {reason}: {count}")
    print("==========================================================")

    return {
        "sample_size": report.sample_size,
        "turnover_correlation": report.turnover_proxy_correlation,
        "complexity_correlation": report.complexity_vs_fitness_correlation,
        "is_proxy_viable": report.is_proxy_viable,
        "total_alphas": total_alphas,
        "degenerate_count": degenerate_count,
        "degenerate_reasons": degenerate_reasons,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-size", type=int, default=500, help="Max sample size for calibration")
    args = ap.parse_args()
    run_study(sample_size=args.sample_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
