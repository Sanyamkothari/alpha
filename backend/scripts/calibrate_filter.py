"""Run calibration backtest of the filter stack against synthetic ground truth.

Usage:
    python -m scripts.calibrate_filter --replications 50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.filter_backtest import run_filter_backtest
from app.services.filter_config import DEFAULT_FILTER_CONFIG, FilterConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replications", type=int, default=50, help="Replications for null and signal runs")
    parser.add_argument("--true-sharpe", type=float, default=1.50, help="True Sharpe for signal families")
    parser.add_argument("--out", type=str, default=None, help="Output directory for scorecard JSON")
    args = parser.parse_args()

    cfg = DEFAULT_FILTER_CONFIG
    print(f"Running filter calibration backtest (Fingerprint: {cfg.fingerprint()})...")
    print(f"  Replications: {args.replications} null + {args.replications} signal (true SR={args.true_sharpe})")

    scorecard = run_filter_backtest(
        n_null_replications=args.replications,
        n_signal_replications=args.replications,
        true_sharpe=args.true_sharpe,
        cfg=cfg,
    )

    print("\n--- Filter Scorecard ---")
    print(f"Config Fingerprint           : {scorecard.config_fingerprint}")
    print(f"Null False Discovery Rate    : {scorecard.null_promotion_rate:.1%} (target < 5.0%)")
    print(f"True Signal Survival Rate    : {scorecard.true_signal_survival_rate:.1%} (target >= 85.0%)")
    print(f"Null Families Promoted       : {scorecard.promoted_null}/{scorecard.null_families}")
    print(f"Signal Families Promoted     : {scorecard.promoted_true_signal}/{scorecard.true_signal_families}")

    if scorecard.stage_attrition:
        print("\nStage Attrition for Unpromoted Signal Families:")
        for stage, count in sorted(scorecard.stage_attrition.items(), key=lambda x: x[1], reverse=True)[:5]:
            print(f"  {stage:<30}: {count} occurrences")

    if args.out:
        out_path = Path(args.out)
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / f"scorecard_{scorecard.config_fingerprint}.json").write_text(
            json.dumps(scorecard.__dict__, indent=2), encoding="utf-8"
        )
        print(f"\nScorecard saved to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
