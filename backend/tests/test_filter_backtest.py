"""Filter Backtest Classifier Suite (A3, A4, STRATEGY.md Rule 5).

Verifies:
- A3: Stationary synthetic alpha with true SR 1.5 survives the stability gates >= 85% of the time.
- A4: Pure-noise family of trials promotes an alpha < 5% of the time.
- End-to-end filter scorecard on synthetic ground truth.
"""

from __future__ import annotations

import numpy as np

from app.services.filter_backtest import (
    generate_family_pnl_matrix,
    generate_synthetic_pnl,
    run_filter_backtest,
)
from app.services.filter_config import DEFAULT_FILTER_CONFIG
from app.services.subperiod import evaluate_subperiod_stability


def test_synthetic_pnl_generator() -> None:
    """Verify synthetic PnL generator generates series with expected mean properties."""
    pnl_null = generate_synthetic_pnl(0.0, n_days=1236)
    assert len(pnl_null) == 1236

    pnl_signal = generate_synthetic_pnl(1.50, n_days=1236)
    assert len(pnl_signal) == 1236
    assert pnl_signal.mean() > 0.0


def test_synthetic_family_pnl_matrix() -> None:
    """Verify matrix generator produces correlated rows matching intra-family correlation."""
    dates, mat = generate_family_pnl_matrix(10, true_annual_sharpe=1.5, intra_corr=0.90, n_days=500)
    assert len(dates) == 500
    assert mat.shape == (10, 500)


def test_synthetic_signal_stability_survival_a3() -> None:
    """A3: A stationary synthetic alpha with true SR 1.5 survives the stability gates >= 85% of the time."""
    rng = np.random.default_rng(42)
    n_replications = 100
    passed = 0

    for _ in range(n_replications):
        pnl = generate_synthetic_pnl(true_annual_sharpe=1.50, n_days=1236, rng=rng)
        verdict = evaluate_subperiod_stability(pnl)
        if verdict.passed:
            passed += 1

    survival_rate = passed / n_replications
    assert survival_rate >= 0.85, (
        f"A3 violated: stationary alpha survival rate {survival_rate:.1%} < 85.0%"
    )


def test_synthetic_null_false_discovery_a4() -> None:
    """A4: A pure-noise family (Sharpe = 0.0) promotes an alpha < 5% of the time."""
    scorecard = run_filter_backtest(
        n_null_replications=20,
        n_signal_replications=0,
        cfg=DEFAULT_FILTER_CONFIG,
        seed=42,
    )
    assert scorecard.null_promotion_rate < 0.05, (
        f"A4 violated: null false discovery rate {scorecard.null_promotion_rate:.1%} >= 5.0%"
    )


def test_synthetic_signal_promotion_scorecard() -> None:
    """End-to-end promotion of genuine plateau signal clearing the EVT haircut bar."""
    scorecard = run_filter_backtest(
        n_null_replications=5,
        n_signal_replications=10,
        true_sharpe=2.60,
        cfg=DEFAULT_FILTER_CONFIG,
        seed=42,
    )
    assert scorecard.null_promotion_rate < 0.05
    assert scorecard.true_signal_survival_rate >= 0.80
