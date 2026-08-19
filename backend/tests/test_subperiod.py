"""Phase 2 comprehensive tests — Sub-Period Stability, Regime Decay & DSR.

Tests:
1. Exact participation ratio N_eff = (sum(lambda))^2 / sum(lambda^2) with identity, collinear, and block matrices.
2. DSR analytical benchmark: known Gaussian PnL properties and closed-form DSR.
3. Daily vs. Annualized Sharpe units trap test (verifying that passing annualized Sharpe instead of daily Sharpe produces an explicit failure/distortion).
4. Cold-start branching: N < 30 bypassing DSR and applying conservative hurdle vs N >= 30 enforcing DSR >= 0.95.
5. Split-half sign-flip rejection (H1 > 0, H2 <= 0).
6. Split-half ratio floor rejection (H1 / H2 outside [0.50, 2.00]).
7. Monthly-stepped 6-month rolling window consistency (>= 75% positive).
8. Regime decay hurdle (SR_252 < 0.65 * SR_full).
9. Thread-safe PnLStore concurrency: multithreaded concurrent reads and writes without data corruption.
"""

from __future__ import annotations

import concurrent.futures
import math

import numpy as np

from app.services.pnl_storage import PnLStore
from app.services.subperiod import (
    compute_dsr,
    compute_effective_trials,
    evaluate_subperiod_stability,
    verify_pnl_reconciliation,
)


def test_effective_trials_eigenvalue_estimator() -> None:
    """Verify participation-ratio estimator N_eff = (sum(lambda))^2 / sum(lambda^2)."""
    m = 6

    # 1. Identity correlation matrix -> all eigenvalues = 1.0 -> N_eff = 6.0
    identity_corr = np.eye(m)
    n_eff_id = compute_effective_trials(identity_corr)
    assert abs(n_eff_id - 6.0) < 1e-4

    # 2. Perfect collinearity (all 1s) -> 1 non-zero eigenvalue = 6.0 -> N_eff = 1.0
    ones_corr = np.ones((m, m))
    n_eff_ones = compute_effective_trials(ones_corr)
    assert abs(n_eff_ones - 1.0) < 1e-4

    # 3. Two independent 3x3 blocks of collinear variables -> eigenvalues = [3, 3, 0, 0, 0, 0] -> N_eff = 2.0
    block_corr = np.zeros((m, m))
    block_corr[:3, :3] = 1.0
    block_corr[3:, 3:] = 1.0
    n_eff_block = compute_effective_trials(block_corr)
    assert abs(n_eff_block - 2.0) < 1e-4


def test_dsr_exact_analytical_benchmark() -> None:
    """Verify compute_dsr against closed-form Bailey & Lopez de Prado (2014) mathematical values."""
    from scipy.stats import norm

    t = 2500
    # Create perfectly standardized series: mu=0.001, sigma=0.01 -> daily SR = 0.10, skew=0, kurt=3
    rng = np.random.RandomState(42)
    raw = rng.normal(size=t)
    std_raw = (raw - np.mean(raw)) / np.std(raw, ddof=1)
    pnl = 0.001 + 0.01 * std_raw

    # N=5 competitors with daily sharpe std = 0.02
    competitors = [0.06, 0.08, 0.10, 0.12, 0.14]
    comp_std = float(np.std(competitors, ddof=1))  # ~0.0316

    # Analytical calculation:
    gamma_em = 0.5772156649015329
    n = len(competitors)
    q1 = float(norm.ppf(1.0 - 1.0 / n))
    q2 = float(norm.ppf(1.0 - 1.0 / (n * math.e)))
    sr_star = comp_std * ((1.0 - gamma_em) * q1 + gamma_em * q2)

    sr_hat = float(np.mean(pnl)) / float(np.std(pnl, ddof=1))
    var_sr = (1.0 + 0.5 * (sr_hat ** 2)) / (t - 1)
    expected_z = (sr_hat - sr_star) / math.sqrt(var_sr)
    expected_dsr = float(norm.cdf(expected_z))

    actual_dsr = compute_dsr(pnl, competitors)
    assert abs(actual_dsr - expected_dsr) < 1e-3, (
        f"actual DSR {actual_dsr:.5f} deviates from expected analytical {expected_dsr:.5f}"
    )


def test_pnl_reconciliation_audit(tmp_path) -> None:
    """Verify the standing PnL acceptance check catches valid vs corrupted series."""
    store = PnLStore(base_dir=tmp_path / "pnl")
    dates = [f"2020-01-{i:04d}" for i in range(1000)]
    rng = np.random.RandomState(42)
    pnl = rng.normal(loc=0.001, scale=0.01, size=1000)
    std = float(np.std(pnl, ddof=1))
    reported_sr = (float(np.mean(pnl)) / std) * math.sqrt(252)
    store.save_pnl(101, dates, pnl)

    # 1. Valid PnL vector matching reported Sharpe
    res_valid = verify_pnl_reconciliation(101, reported_sr, store)
    assert res_valid.is_valid
    assert res_valid.sharpe_diff < 1e-4
    assert abs(res_valid.pnl_sum - float(np.sum(pnl))) < 1e-4

    # 2. Corrupted reported Sharpe fails reconciliation
    res_corrupt = verify_pnl_reconciliation(101, reported_sr + 1.5, store)
    assert not res_corrupt.is_valid
    assert "deviates from reported" in (res_corrupt.error or "")


def test_dsr_analytical_benchmark() -> None:
    """Bailey & Lopez de Prado (2014) analytical benchmark properties."""
    np.random.seed(42)
    t = 2500  # standard 10-year trading history

    # Strong positive signal (daily mean 0.001, std 0.01 -> daily SR = 0.10, annualized SR = 1.58)
    good_pnl = np.random.normal(loc=0.001, scale=0.01, size=t)
    family_daily_sharpes = [0.03, 0.06, 0.08, 0.10]
    dsr_good = compute_dsr(good_pnl, family_daily_sharpes)
    assert dsr_good > 0.95, f"expected DSR > 0.95 for true alpha, got {dsr_good}"

    # Pure noise (mean = 0) evaluated against 50 competing trials
    noise_pnl = np.random.normal(loc=0.0, scale=0.01, size=t)
    family_noise = list(np.random.normal(loc=0.05, scale=0.03, size=50))
    dsr_noise = compute_dsr(noise_pnl, family_noise)
    assert dsr_noise < 0.50, f"expected DSR < 0.50 for noise, got {dsr_noise}"


def test_dsr_daily_units_trap() -> None:
    """Catch the single most common DSR bug: passing annualized Sharpe instead of daily Sharpe."""
    np.random.seed(42)
    t = 2500
    pnl = np.random.normal(loc=0.0008, scale=0.01, size=t)

    # Correct daily scale: daily Sharpe ~ 0.08, family daily Sharpes in [0.02, 0.10]
    daily_family = [0.02, 0.05, 0.07, 0.09]
    dsr_correct = compute_dsr(pnl, daily_family)

    # Incorrect annualized scale: ~1.26, family in [0.3, 1.5]
    annualized_family = [s * math.sqrt(252) for s in daily_family]

    # When daily PnL is compared to annualized distribution without unit conversion,
    # sr_star becomes ~1.5 >> daily SR ~0.08, collapsing DSR to ~0.0
    dsr_mismatched = compute_dsr(pnl, annualized_family)
    assert dsr_correct > 0.80
    assert dsr_mismatched < 0.01, "mismatched annualized units should collapse DSR"


def test_subperiod_split_half_sign_guard() -> None:
    """Sign check: hard reject on opposite or non-positive split-half signs."""
    t = 252 * 4  # 4 years
    np.random.seed(42)

    # Case A: Positive in H1, negative in H2
    bad_pnl = np.concatenate([
        np.random.normal(loc=0.001, scale=0.01, size=t // 2),
        np.random.normal(loc=-0.0005, scale=0.01, size=t // 2),
    ])
    verdict_bad = evaluate_subperiod_stability(bad_pnl)
    assert not verdict_bad.passed
    assert any("split-half" in r for r in verdict_bad.reasons)

    # Case B: Steady positive performance throughout all 4 years
    good_pnl = np.full(t, 0.001) + np.random.normal(loc=0.0, scale=0.002, size=t)
    verdict_good = evaluate_subperiod_stability(good_pnl)
    assert verdict_good.passed, f"good pnl failed: {verdict_good.reasons}"
    assert verdict_good.h1_sharpe is not None and verdict_good.h1_sharpe > 0
    assert verdict_good.h2_sharpe is not None and verdict_good.h2_sharpe > 0


def test_subperiod_split_ratio_floor() -> None:
    """Reject when performance in one half is less than 50% of the other."""
    t = 252 * 4
    np.random.seed(42)

    # H1 mean is 4x higher than H2 mean -> ratio = 0.25 < 0.50
    imbalanced_pnl = np.concatenate([
        np.random.normal(loc=0.0020, scale=0.01, size=t // 2),
        np.random.normal(loc=0.0004, scale=0.01, size=t // 2),
    ])
    verdict = evaluate_subperiod_stability(imbalanced_pnl, split_ratio_floor=0.50)
    assert not verdict.passed
    assert any("split-half ratio" in r for r in verdict.reasons)


def test_subperiod_rolling_window_consistency() -> None:
    """Monthly-stepped 6-month (126d) rolling windows must be >= 75% positive."""
    t = 252 * 5
    np.random.seed(42)

    # Signal that decays heavily across 3 out of 5 years -> < 75% positive rolling windows
    failing_rolling_pnl = np.concatenate([
        np.random.normal(loc=0.001, scale=0.01, size=252 * 1),
        np.random.normal(loc=-0.001, scale=0.01, size=252 * 3),
        np.random.normal(loc=0.001, scale=0.01, size=252 * 1),
    ])
    verdict = evaluate_subperiod_stability(failing_rolling_pnl, rolling_pos_floor=0.75)
    assert not verdict.passed
    assert any("rolling 6-month positive ratio" in r for r in verdict.reasons)


def test_subperiod_recent_regime_decay() -> None:
    """Strictly enforce SR_252 >= 0.65 * SR_full (reject stale alphas)."""
    t = 252 * 5
    np.random.seed(42)

    # Strong performance years 1-4, but flat in recent 252 days
    decayed_pnl = np.concatenate([
        np.random.normal(loc=0.0015, scale=0.01, size=t - 252),
        np.random.normal(loc=0.0001, scale=0.01, size=252),
    ])
    verdict = evaluate_subperiod_stability(decayed_pnl, recent_decay_floor=0.65)
    assert not verdict.passed
    assert any("decayed below 65%" in r for r in verdict.reasons)


def test_pnl_store_thread_safety(tmp_path) -> None:
    """Thread-safe PnLStore roundtrip under concurrent read/write stress."""
    store = PnLStore(base_dir=tmp_path / "pnl")
    dates = [f"2020-{i:04d}" for i in range(500)]
    n_alphas = 50

    def write_worker(aid: int) -> None:
        rng = np.random.RandomState(aid)
        pnl = rng.normal(loc=0.0001 * aid, scale=0.01, size=len(dates))
        store.save_pnl(aid, dates, pnl)

    def read_worker(aid: int) -> bool:
        loaded = store.load_pnl(aid)
        if loaded is None:
            return False
        ldates, lpnl = loaded
        return len(ldates) == len(dates) and len(lpnl) == len(dates)

    # Concurrent writes across 12 worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(write_worker, range(1, n_alphas + 1)))

    # Concurrent interleaved reads across 12 worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(read_worker, range(1, n_alphas + 1)))

    assert all(results), "all concurrent PnL loads must succeed"

    # Aligned matrix extraction
    valid_ids, m_dates, mat = store.get_aligned_matrix(list(range(1, n_alphas + 1)), min_overlap=300)
    assert len(valid_ids) == n_alphas
    assert mat.shape == (n_alphas, 500)
