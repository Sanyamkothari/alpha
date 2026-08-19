"""Filter Backtest Classifier Suite (A3, A4, STRATEGY.md Rule 5).

Verifies:
- A3: Stationary synthetic alpha with true SR 1.5 survives the stability gates >= 85% of the time.
- A4: Pure-noise family of trials promotes an alpha < 5% of the time.
- End-to-end filter scorecard on synthetic ground truth.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.filter_backtest import (
    generate_family_pnl_matrix,
    generate_synthetic_pnl,
    run_filter_backtest,
)
from app.services.filter_config import DEFAULT_FILTER_CONFIG, FilterConfig
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
    """A4: a pure-noise family promotes < 5% of the time.

    Sixty replications is the floor at which the claim is even expressible: zero
    promotions out of twenty bounds the rate at 14%, which is not a measurement
    of "< 5%", it is a measurement of "we did not look hard enough". The
    500-replication run lives in the slow lane below.

    Read this together with the power test. A gate that promotes NOTHING scores a
    perfect false-discovery rate, so this assertion is meaningless on its own --
    it is a constraint, and the power test is the objective.
    """
    scorecard = run_filter_backtest(
        n_null_replications=60,
        n_signal_replications=0,
        cfg=DEFAULT_FILTER_CONFIG,
        seed=42,
    )
    assert scorecard.null_promotion_rate < 0.05, (
        f"A4 violated: null false discovery rate {scorecard.null_promotion_rate:.1%} >= 5.0%"
    )
    assert scorecard.null_rate_ci95[1] < 0.10, (
        f"the sample must be able to support the claim: 95% upper bound is "
        f"{scorecard.null_rate_ci95[1]:.1%}"
    )


def test_filter_is_not_a_degenerate_classifier() -> None:
    """The filter must promote real alphas, not merely refuse to promote fake ones.

    This is the test that would have caught the shipped operating point. With the
    bar as target + selection-inflation, power at true Sharpe 1.5 measured 0% at
    every programme size -- while the false-discovery rate read a flawless 0%,
    because a gate that never promotes cannot promote a false one.
    """
    scorecard = run_filter_backtest(
        n_null_replications=20,
        n_signal_replications=40,
        true_sharpe=1.50,
        programme_trials=400,
        cfg=DEFAULT_FILTER_CONFIG,
        seed=7,
    )
    assert scorecard.true_signal_survival_rate >= 0.80, (
        f"power at true SR 1.5 is {scorecard.true_signal_survival_rate:.1%} -- the "
        f"filter is rejecting real alphas, whatever its false-discovery rate says"
    )
    assert scorecard.null_promotion_rate < 0.10


def test_stage_attrition_names_the_binding_gate() -> None:
    """The scorecard must say WHERE candidates die, not just how many survive.

    "Nothing promotes" is equally consistent with a bar set too high and a
    stability test set too strict, and those want opposite fixes. Without this,
    every threshold argument is unsettleable.
    """
    scorecard = run_filter_backtest(
        n_null_replications=10,
        n_signal_replications=0,
        programme_trials=4000,
        cfg=DEFAULT_FILTER_CONFIG,
        seed=3,
    )
    family_stages = {k: v for k, v in scorecard.stage_attrition.items() if k.startswith("null:")}
    assert family_stages, "stage_attrition must record a per-family outcome"
    assert sum(family_stages.values()) == 10, "every replication must be accounted for"


@pytest.mark.slow
def test_synthetic_null_false_discovery_500_replications() -> None:
    """The number of record for A4. Slow lane; run nightly."""
    scorecard = run_filter_backtest(
        n_null_replications=500,
        n_signal_replications=0,
        cfg=DEFAULT_FILTER_CONFIG,
        seed=11,
    )
    assert scorecard.null_rate_ci95[1] < 0.05, (
        f"A4 at 500 replications: rate {scorecard.null_promotion_rate:.3%}, "
        f"95% upper bound {scorecard.null_rate_ci95[1]:.3%}"
    )


@pytest.mark.slow
def test_power_decays_with_programme_size() -> None:
    """Searching more costs you real alphas. The tradeoff should be measured, not assumed.

    The bar rises with the number of trials the winner was selected from, so a
    programme that mass-produces correlated trials pays for them in power. This is
    the quantitative argument for fewer, more diverse mechanisms over more of the
    same one.
    """
    small = run_filter_backtest(
        n_null_replications=0, n_signal_replications=60, true_sharpe=1.50,
        programme_trials=400, seed=7,
    )
    large = run_filter_backtest(
        n_null_replications=0, n_signal_replications=60, true_sharpe=1.50,
        programme_trials=4000, seed=7,
    )
    assert small.true_signal_survival_rate > large.true_signal_survival_rate, (
        f"power at 400 trials ({small.true_signal_survival_rate:.1%}) should exceed "
        f"power at 4000 ({large.true_signal_survival_rate:.1%})"
    )
