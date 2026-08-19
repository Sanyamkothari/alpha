"""Tests for Phase 5: Statistics & Diagnostics (E1 CSCV/PBO, E2 neutralization robustness).

E1 is verified on a *heterogeneous* matrix — one strategy with a genuine edge among
noise. An earlier version of this test drew the "signal" case as
``normal(0.20, 1.0, size=(49, 1250))``, giving all 49 strategies the same true mean:
there is no better strategy to find, so PBO ~ 0.5 is the correct answer and the test
could not distinguish a working implementation from a constant.

No wall-clock assertions live here. CSCV timing is a property of the runner, not of
the code.
"""

from __future__ import annotations

import math
import statistics
import time

import numpy as np
import pytest

from app.services.cscv import MIN_BLOCK_LEN, MIN_STRATEGIES, compute_pbo_cscv
from app.services.perturbation import (
    NEUTRALIZATION_LADDER,
    adjacent_neutralizations,
    check_neutralization_robustness,
)
from app.services.plateau import SurfacePoint, _structure_of

# ---------------------------------------------------------------------------
# E1 — CSCV / PBO
# ---------------------------------------------------------------------------


def _matrix(rng: np.random.Generator, *, edge: float = 0.0) -> np.ndarray:
    """49 strategies x 1250 days; when ``edge`` is set, strategy 0 alone has it."""
    mat = rng.normal(0.0, 1.0, size=(49, 1250))
    if edge:
        mat[0] += edge
    return mat


def test_e1_pbo_detects_a_genuine_edge() -> None:
    """One truly superior strategy => the IS winner holds up OOS => PBO at the floor."""
    for seed in range(5):
        res = compute_pbo_cscv(_matrix(np.random.default_rng(seed), edge=0.25))
        assert not res.degraded
        assert res.n_splits == 12870  # comb(16, 8)
        assert res.pbo == pytest.approx(0.0, abs=0.02), f"seed {seed}: pbo={res.pbo}"


def test_e1_cscv_scales_with_strategy_count() -> None:
    """CSCV must stay vectorised: doubling the strategy count must not triple the cost."""
    rng = np.random.default_rng(7)
    t_days = 1250

    def _time_for(n_strats: int) -> float:
        mat = rng.normal(0.0, 1.0, size=(n_strats, t_days))
        compute_pbo_cscv(mat, n_subperiods=10)
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            compute_pbo_cscv(mat, n_subperiods=10)
            best = min(best, time.perf_counter() - t0)
        return best

    small = _time_for(25)
    large = _time_for(50)

    assert large < small * 3.0, (
        f"CSCV cost grew {large / small:.1f}x for 2x the strategies "
        "— the split loop is probably no longer vectorised"
    )


def test_e1_pbo_on_pure_noise_averages_one_half() -> None:
    """No edge to find => the IS winner is a coin flip OOS => PBO ~ 0.5 in expectation.

    Asserted on the mean across seeds, not a single draw: a single family's PBO is a
    genuinely noisy statistic (observed range ~0.19-0.89 over 12 seeds), which is
    itself the reason PBO must not be gated on from one family.
    """
    pbos = [compute_pbo_cscv(_matrix(np.random.default_rng(s))).pbo for s in range(12)]
    assert statistics.mean(pbos) == pytest.approx(0.5, abs=0.1)
    # And the statistic must actually vary — a constant would also pass a mean check.
    assert statistics.pstdev(pbos) > 0.05


def test_e1_pbo_separates_edge_from_noise() -> None:
    """The discrimination claim itself, on matched seeds."""
    for seed in range(5):
        noise = compute_pbo_cscv(_matrix(np.random.default_rng(seed)))
        edge = compute_pbo_cscv(_matrix(np.random.default_rng(seed), edge=0.25))
        assert edge.pbo < noise.pbo, f"seed {seed}: edge={edge.pbo} noise={noise.pbo}"


def test_e1_degrades_instead_of_reporting_zero() -> None:
    """Too few strategies or too-short blocks must not read as 'no overfitting'."""
    rng = np.random.default_rng(0)

    too_few = compute_pbo_cscv(rng.normal(0, 1, (MIN_STRATEGIES - 1, 1250)))
    assert too_few.degraded and not too_few.reportable
    assert math.isnan(too_few.pbo), "degraded PBO must be nan, never 0.0"
    assert "strategies" in (too_few.degraded_reason or "")

    short_blocks = compute_pbo_cscv(rng.normal(0, 1, (49, MIN_BLOCK_LEN * 16 - 16)))
    assert short_blocks.degraded and math.isnan(short_blocks.pbo)
    assert "block length" in (short_blocks.degraded_reason or "")

    # The original failing case: N=2, T=20 previously returned pbo=0.0 alongside a
    # median OOS Sharpe of 3.57 computed from one-day blocks.
    degenerate = compute_pbo_cscv(rng.normal(0, 1, (2, 20)))
    assert degenerate.degraded and math.isnan(degenerate.pbo)


def test_e1_healthy_input_is_reportable() -> None:
    res = compute_pbo_cscv(_matrix(np.random.default_rng(3)))
    assert res.reportable and not res.degraded and math.isfinite(res.pbo)


# ---------------------------------------------------------------------------
# E2 — neutralization-ladder robustness
# ---------------------------------------------------------------------------


def _point(
    alpha_id: int, neutralization: str, sharpe: float, *, window: int = 20, decay: int = 2
) -> SurfacePoint:
    grid = {
        "ts": "ts_zscore", "cs": "rank", "group": None,
        "neutralization": neutralization, "truncation": 0.08,
        "universe": "TOP3000", "hump": None,
        "window": window, "decay": decay,
    }
    return SurfacePoint(
        alpha_id=alpha_id, expression=f"e{alpha_id}", window=window, decay=decay,
        sharpe=sharpe, turnover=0.3, fitness=1.5, passed_all_checks=True,
        structure=_structure_of(grid),
    )


def test_e2_ladder_adjacency() -> None:
    assert adjacent_neutralizations("NONE") == ["MARKET"]
    assert adjacent_neutralizations("SUBINDUSTRY") == ["INDUSTRY"]
    assert adjacent_neutralizations("SECTOR") == ["MARKET", "INDUSTRY"]
    assert adjacent_neutralizations("NOT_A_RUNG") == []
    assert set(NEUTRALIZATION_LADDER) >= {"NONE", "SECTOR", "SUBINDUSTRY"}


def test_e2_robust_mechanism_survives_one_rung() -> None:
    peak = _point(1, "INDUSTRY", 2.10)
    family = [peak, _point(2, "SECTOR", 1.95), _point(3, "SUBINDUSTRY", 2.00)]

    res = check_neutralization_robustness(peak, family)
    assert res.measured and res.is_robust
    assert set(res.neighbour_sharpes) == {"SECTOR", "SUBINDUSTRY"}
    assert res.retention == pytest.approx(1.975 / 2.10, abs=1e-6)


def test_e2_neutralization_fit_is_caught() -> None:
    """A signal that only works at one rung is a fit to that rung's residual structure."""
    peak = _point(10, "INDUSTRY", 2.40)
    family = [peak, _point(11, "SECTOR", 0.30), _point(12, "SUBINDUSTRY", 0.20)]

    res = check_neutralization_robustness(peak, family)
    assert res.measured and res.is_robust is False
    assert "neutralization-fragile" in res.reason


def test_e2_unmeasured_is_not_fragile() -> None:
    """A missing neighbour is a gap in our coverage, not evidence against the alpha."""
    peak = _point(20, "INDUSTRY", 2.10)

    res = check_neutralization_robustness(peak, [peak])
    assert res.is_robust is None and not res.measured
    assert "not measured" in res.reason

    # Adjacent surface exists but at a different coordinate — still not comparable.
    off_coord = _point(21, "SECTOR", 1.95, window=40, decay=2)
    assert check_neutralization_robustness(peak, [peak, off_coord]).is_robust is None


def test_e2_ignores_points_differing_on_another_axis() -> None:
    """Matching must be exact on every structural axis except neutralization."""
    peak = _point(30, "INDUSTRY", 2.10)
    other = _point(31, "SECTOR", 1.95)
    mismatched_grid = {
        "ts": "ts_zscore", "cs": "rank", "group": None, "neutralization": "SECTOR",
        "truncation": 0.01, "universe": "TOP3000", "hump": None, "window": 20, "decay": 2,
    }
    mismatched = SurfacePoint(
        alpha_id=32, expression="e32", window=20, decay=2, sharpe=1.95,
        turnover=0.3, fitness=1.5, passed_all_checks=True,
        structure=_structure_of(mismatched_grid),
    )

    assert check_neutralization_robustness(peak, [peak, mismatched]).is_robust is None
    assert check_neutralization_robustness(peak, [peak, other]).measured
