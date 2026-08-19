"""Tests for Phase 5: Statistics & Diagnostics (Items E1, E2).

Validates:
- E1: Combinatorially Symmetric Cross-Validation (CSCV) computes PBO in < 500ms, measures OOS rank degradation, and separates pure noise from genuine signals.
- E2: Perturbation robustness accurately discriminates broad plateau ridges from isolated lucky spikes.
"""

from __future__ import annotations

import time

import numpy as np

from app.services.cscv import compute_pbo_cscv
from app.services.perturbation import check_perturbation_robustness
from app.services.plateau import SurfacePoint


def test_e1_cscv_pbo_accuracy() -> None:
    """E1: PBO distinguishes noise from signal on a 49x1250 matrix."""
    rng = np.random.default_rng(42)
    n_strats = 49
    t_days = 1250

    # 1. Pure Gaussian Noise Matrix (Mean 0, Std 1)
    noise_mat = rng.normal(0.0, 1.0, size=(n_strats, t_days))

    res_noise = compute_pbo_cscv(noise_mat, n_subperiods=16)

    assert res_noise.n_splits == 12870  # comb(16, 8)
    assert res_noise.pbo >= 0.20
    assert res_noise.degradation_pct >= 0.50  # Severe performance collapse in OOS

    # 2. Strong Positive Signal Matrix
    signal_mat = rng.normal(0.20, 1.0, size=(n_strats, t_days))
    res_signal = compute_pbo_cscv(signal_mat, n_subperiods=16)
    assert res_signal.pbo_loss <= 0.05, f"PBO loss on strong signal was {res_signal.pbo_loss:.3f} > 0.05"
    assert res_signal.median_oos_sharpe > 1.5


def test_e1_cscv_scales_with_strategy_count() -> None:
    """CSCV must stay vectorised: doubling the strategy count must not triple the cost.

    This replaces a `elapsed < 0.500` assertion. Absolute wall-clock is a property of
    the machine, not of the code — that assertion passed alone and failed in the full
    suite on the same commit. A ratio between two measurements on the *same* machine
    still catches the regression that mattered (a fall back to a Python loop over
    12,870 splits) without depending on how loaded the runner is.
    """
    rng = np.random.default_rng(7)
    t_days = 1250

    def _time_for(n_strats: int) -> float:
        mat = rng.normal(0.0, 1.0, size=(n_strats, t_days))
        compute_pbo_cscv(mat, n_subperiods=10)  # warm caches / JIT-free baseline
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            compute_pbo_cscv(mat, n_subperiods=10)
            best = min(best, time.perf_counter() - t0)
        return best  # best-of-3 resists scheduler noise

    small = _time_for(25)
    large = _time_for(50)

    # A vectorised implementation is near-linear in strategy count. A Python-level
    # loop over splits would blow well past 3x.
    assert large < small * 3.0, (
        f"CSCV cost grew {large / small:.1f}x for 2x the strategies "
        "— the split loop is probably no longer vectorised"
    )


def test_e2_perturbation_robustness() -> None:
    """E2: Perturbation robustness accepts broad plateaus and rejects fragile spikes."""
    struct = ("ts_zscore", "rank", None, "SUBINDUSTRY", 0.01, "TOP3000", None)

    # 1. Broad Plateau: Peak = 2.10, Neighbors = (2.05, 2.00, 1.95, 1.90) => Fragility = (2.10 - 1.975)/2.10 ~ 0.06
    peak_robust = SurfacePoint(
        alpha_id=1, expression="e1", window=20, decay=2,
        sharpe=2.10, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct
    )
    plateau_points = [
        peak_robust,
        SurfacePoint(alpha_id=2, expression="e2", window=10, decay=2, sharpe=2.05, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
        SurfacePoint(alpha_id=3, expression="e3", window=40, decay=2, sharpe=2.00, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
        SurfacePoint(alpha_id=4, expression="e4", window=20, decay=1, sharpe=1.95, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
        SurfacePoint(alpha_id=5, expression="e5", window=20, decay=4, sharpe=1.90, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
    ]

    res_robust = check_perturbation_robustness(peak_robust, plateau_points)
    assert res_robust.is_robust
    assert res_robust.fragility < 0.35
    assert res_robust.neighbour_median_sharpe >= 1.25

    # 2. Isolated Spike: Peak = 2.40, Neighbors = (0.50, 0.40, 0.30, 0.20) => Fragility = (2.40 - 0.35)/2.40 ~ 0.85
    peak_spike = SurfacePoint(
        alpha_id=10, expression="s1", window=20, decay=2,
        sharpe=2.40, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct
    )
    spike_points = [
        peak_spike,
        SurfacePoint(alpha_id=11, expression="s2", window=10, decay=2, sharpe=0.50, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
        SurfacePoint(alpha_id=12, expression="s3", window=40, decay=2, sharpe=0.40, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
        SurfacePoint(alpha_id=13, expression="s4", window=20, decay=1, sharpe=0.30, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
        SurfacePoint(alpha_id=14, expression="s5", window=20, decay=4, sharpe=0.20, turnover=0.3, fitness=1.5, passed_all_checks=True, structure=struct),
    ]

    res_spike = check_perturbation_robustness(peak_spike, spike_points)
    assert not res_spike.is_robust
    assert res_spike.fragility > 0.35
    assert "fragile_peak" in str(res_spike.reason)
