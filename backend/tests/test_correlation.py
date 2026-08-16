"""Phase 3 comprehensive tests — Empirical Returns Correlation Engine & Gates.

Tests:
1. Exact pairwise Pearson correlation math (collinear, anti-correlated, orthogonal).
2. Date intersection logic with irregular calendars and date offsets.
3. Overlap hurdle rejection: candidates with < 500 overlapping days bypass empirical gating.
4. Internal correlation threshold gate: collision triggers at >= 0.55, passes at < 0.55.
5. Structural proxy fallback: when empirical PnL is absent, catches skeleton hash duplicates.
6. Vectorized N x N matrix calculation across portfolios.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.services.correlation import (
    compute_correlation_matrix,
    compute_pairwise_correlation,
    check_portfolio_empirical_correlation,
)
from app.services.pnl_storage import PnLStore


def test_pairwise_correlation_math() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0] * 20)
    y = np.array([2.0, 4.0, 6.0, 8.0, 10.0] * 20)
    corr_pos = compute_pairwise_correlation(x, y)
    assert abs(corr_pos - 1.0) < 1e-4

    z = -x
    corr_neg = compute_pairwise_correlation(x, z)
    assert abs(corr_neg - (-1.0)) < 1e-4

    # Orthogonal vectors
    np.random.seed(42)
    a = np.random.normal(size=1000)
    b = np.random.normal(size=1000)
    corr_ortho = compute_pairwise_correlation(a, b)
    assert abs(corr_ortho) < 0.10


def test_date_intersection_alignment(tmp_path) -> None:
    store = PnLStore(base_dir=tmp_path / "pnl")

    # Alpha 1 dates: days 0..499
    dates1 = [f"d_{i:04d}" for i in range(500)]
    pnl1 = np.ones(500)
    store.save_pnl(1, dates1, pnl1)

    # Alpha 2 dates: days 200..699 (300 overlapping days)
    dates2 = [f"d_{i:04d}" for i in range(200, 700)]
    pnl2 = np.ones(500)
    store.save_pnl(2, dates2, pnl2)

    valid_ids, common_dates, mat = store.get_aligned_matrix([1, 2], min_overlap=250)
    assert len(valid_ids) == 2
    assert len(common_dates) == 300
    assert common_dates[0] == "d_0200"
    assert common_dates[-1] == "d_0499"


def test_sub_500_day_overlap_rejection(tmp_path, db_session) -> None:
    store = PnLStore(base_dir=tmp_path / "pnl")

    a1 = Alpha(
        expression="rank(close)",
        expression_hash="corr_sub500_1",
        status=AlphaStatus.SUBMITTED.value,
        is_valid=True,
    )
    a2 = Alpha(
        expression="zscore(close)",
        expression_hash="corr_sub500_2",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add_all([a1, a2])
    db_session.commit()

    # Only 200 common dates (< 500 minimum overlap)
    dates = [f"day_{i:04d}" for i in range(200)]
    pnl = np.random.normal(size=200)
    store.save_pnl(a1.id, dates, pnl)
    store.save_pnl(a2.id, dates, pnl)

    # Should NOT trigger empirical correlation due to insufficient overlap
    is_corr, reason, max_corr = check_portfolio_empirical_correlation(
        db_session, a2.id, pnl_store=store, min_overlap=500
    )
    assert not is_corr


def test_portfolio_correlation_gate_threshold(tmp_path, db_session) -> None:
    store = PnLStore(base_dir=tmp_path / "pnl")

    # Submitted Alpha in portfolio
    port_alpha = Alpha(
        expression="rank(ts_delta(close, 10))",
        expression_hash="corr_port_1",
        status=AlphaStatus.SUBMITTED.value,
        is_valid=True,
    )
    # High correlation candidate (rho ~ 0.85 >= 0.55)
    cand_high = Alpha(
        expression="rank(ts_delta(close, 12))",
        expression_hash="corr_cand_high",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    # Orthogonal candidate (rho ~ 0.05 < 0.55)
    cand_low = Alpha(
        expression="rank(ts_zscore(volume, 60))",
        expression_hash="corr_cand_low",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add_all([port_alpha, cand_high, cand_low])
    db_session.flush()

    dates = [f"d_{i:04d}" for i in range(600)]
    np.random.seed(42)
    base_pnl = np.random.normal(loc=0.001, scale=0.01, size=600)
    high_pnl = base_pnl + np.random.normal(loc=0.0, scale=0.004, size=600)  # rho ~ 0.92
    low_pnl = np.random.normal(loc=0.001, scale=0.01, size=600)  # rho ~ 0.0

    store.save_pnl(port_alpha.id, dates, base_pnl)
    store.save_pnl(cand_high.id, dates, high_pnl)
    store.save_pnl(cand_low.id, dates, low_pnl)

    # 1. High correlation candidate -> REJECTED
    is_corr_h, reason_h, max_corr_h = check_portfolio_empirical_correlation(
        db_session, cand_high.id, pnl_store=store, threshold=0.55, min_overlap=500
    )
    assert is_corr_h
    assert max_corr_h is not None and max_corr_h >= 0.55
    assert "empirical correlation" in (reason_h or "")

    # 2. Low correlation candidate -> PASSED
    is_corr_l, reason_l, max_corr_l = check_portfolio_empirical_correlation(
        db_session, cand_low.id, pnl_store=store, threshold=0.55, min_overlap=500
    )
    assert not is_corr_l
    assert reason_l is None


def test_structural_fallback_when_pnl_missing(db_session, tmp_path) -> None:
    store = PnLStore(tmp_path / "pnl")
    # Portfolio alpha with structural hash
    port_alpha = Alpha(
        expression="rank(ts_delta(close, 5))",
        expression_hash="struct_fall_port",
        family_key="close_struct_fall@USA/TOP3000/d1",
        feature_json={"structural_hash": "struct_abc123"},
        status=AlphaStatus.SUBMITTED.value,
        is_valid=True,
    )
    # Candidate with matching structural hash on the same field
    cand_alpha = Alpha(
        expression="zscore(ts_delta(close, 5))",
        expression_hash="struct_fall_cand",
        family_key="close_struct_fall@USA/TOP3000/d1",
        feature_json={"structural_hash": "struct_abc123"},
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add_all([port_alpha, cand_alpha])
    db_session.flush()

    # Empirical PnL missing -> must fallback to structural check
    is_corr, reason, _ = check_portfolio_empirical_correlation(db_session, cand_alpha.id, pnl_store=store)
    assert is_corr
    assert "structural correlation collision" in (reason or "")


def test_correlation_matrix_vectorization() -> None:
    mat = np.array([
        [1.0, 2.0, 3.0, 4.0, 5.0] * 10,
        [5.0, 4.0, 3.0, 2.0, 1.0] * 10,
        [1.0, 0.0, 1.0, 0.0, 1.0] * 10,
    ])
    res = compute_correlation_matrix(mat)
    assert res.shape == (3, 3)
    assert np.allclose(np.diag(res), 1.0)
    assert abs(res[0, 1] - (-1.0)) < 1e-4
