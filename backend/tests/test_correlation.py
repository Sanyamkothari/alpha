"""Phase 3 comprehensive tests — Empirical Returns Correlation Engine & Gates.

Tests:
1. Exact pairwise Pearson correlation math (collinear, anti-correlated, orthogonal).
2. Date intersection logic with irregular calendars and date offsets.
3. Overlap hurdle rejection: candidates with < 500 overlapping days fail closed.
4. Internal correlation threshold gate: collision triggers at >= 0.55, passes at < 0.55.
5. Structural proxy fallback: when empirical PnL is absent, catches skeleton hash duplicates.
6. Vectorized N x N matrix calculation across portfolios.
7. Unmeasured correlation fails closed per CLAUDE.md and audit §1.5.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.enums import AlphaStatus, PlatformOutcome
from app.services.correlation import (
    CorrelationVerdict,
    check_portfolio_empirical_correlation,
    compute_correlation_matrix,
    compute_pairwise_correlation,
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
    rng = np.random.default_rng(42)
    a = rng.normal(size=1000)
    b = rng.normal(size=1000)
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
        platform_outcome=PlatformOutcome.SUBMITTED.value,
        is_valid=True,
    )
    a2 = Alpha(
        expression="zscore(close)",
        expression_hash="corr_sub500_2",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add_all([a1, a2])
    db_session.flush()
    db_session.add(SubmissionAttempt(alpha_id=a1.id, result="submitted"))
    db_session.flush()

    # Only 200 common dates (< 500 minimum overlap)
    dates = [f"day_{i:04d}" for i in range(200)]
    rng = np.random.default_rng(1)
    pnl = rng.normal(size=200)
    store.save_pnl(a1.id, dates, pnl)
    store.save_pnl(a2.id, dates, pnl)

    # Fails closed on insufficient overlap
    v = check_portfolio_empirical_correlation(
        db_session, a2.id, pnl_store=store, portfolio=[a1], min_overlap=500
    )
    assert isinstance(v, CorrelationVerdict)
    assert v.blocking is True
    assert v.method == "unmeasured"
    assert v.skipped_pairs == 1


def test_portfolio_correlation_gate_threshold(tmp_path, db_session) -> None:
    store = PnLStore(base_dir=tmp_path / "pnl")

    port_alpha = Alpha(
        expression="rank(ts_delta(close, 10))",
        expression_hash="corr_port_1",
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.SUBMITTED.value,
        is_valid=True,
    )
    cand_high = Alpha(
        expression="rank(ts_delta(close, 12))",
        expression_hash="corr_cand_high",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    cand_low = Alpha(
        expression="rank(ts_zscore(volume, 60))",
        expression_hash="corr_cand_low",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add_all([port_alpha, cand_high, cand_low])
    db_session.flush()
    db_session.add(SubmissionAttempt(alpha_id=port_alpha.id, result="submitted"))
    db_session.flush()

    dates = [f"d_{i:04d}" for i in range(600)]
    rng = np.random.default_rng(42)
    base_pnl = rng.normal(loc=0.001, scale=0.01, size=600)
    high_pnl = base_pnl + rng.normal(loc=0.0, scale=0.004, size=600)
    low_pnl = rng.normal(loc=0.001, scale=0.01, size=600)

    store.save_pnl(port_alpha.id, dates, base_pnl)
    store.save_pnl(cand_high.id, dates, high_pnl)
    store.save_pnl(cand_low.id, dates, low_pnl)

    # 1. High correlation candidate -> BLOCKED
    vh = check_portfolio_empirical_correlation(
        db_session,
        cand_high.id,
        pnl_store=store,
        portfolio=[port_alpha],
        threshold=0.55,
        min_overlap=500,
    )
    assert vh.blocking is True
    assert vh.max_correlation is not None and vh.max_correlation >= 0.55
    assert "empirical correlation" in (vh.reason or "")
    assert vh.method == "empirical"

    # 2. Low correlation candidate -> PASSED
    vl = check_portfolio_empirical_correlation(
        db_session,
        cand_low.id,
        pnl_store=store,
        portfolio=[port_alpha],
        threshold=0.55,
        min_overlap=500,
    )
    assert vl.blocking is False
    assert vl.reason is None
    assert vl.method == "empirical"


def test_structural_fallback_when_pnl_missing(db_session, tmp_path) -> None:
    store = PnLStore(tmp_path / "pnl")
    port_alpha = Alpha(
        expression="rank(ts_delta(close, 5))",
        expression_hash="struct_fall_port",
        family_key="close_struct_fall@USA/TOP3000/d1",
        feature_json={"structural_hash": "struct_abc123"},
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.SUBMITTED.value,
        is_valid=True,
    )
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
    db_session.add(SubmissionAttempt(alpha_id=port_alpha.id, result="submitted"))
    db_session.flush()

    v = check_portfolio_empirical_correlation(
        db_session, cand_alpha.id, pnl_store=store, portfolio=[port_alpha]
    )
    assert v.blocking is True
    assert v.method == "structural_proxy"
    assert "structural correlation collision" in (v.reason or "")


def test_unmeasured_correlation_blocks(db_session, tmp_path) -> None:
    """Audit §1.5: a constraint that could not be evaluated is not a passed one."""
    store = PnLStore(tmp_path / "pnl")
    port = Alpha(
        expression="rank(ts_delta(close, 5))",
        expression_hash="unmeas_port",
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.SUBMITTED.value,
        is_valid=True,
    )
    cand = Alpha(
        expression="rank(ts_delta(volume, 5))",
        expression_hash="unmeas_cand",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add_all([port, cand])
    db_session.flush()
    dates = [f"d_{i:04d}" for i in range(600)]
    store.save_pnl(port.id, dates, np.ones(600))

    v = check_portfolio_empirical_correlation(
        db_session, cand.id, pnl_store=store, portfolio=[port]
    )
    assert v.blocking is True
    assert v.method == "unmeasured"
    assert v.max_correlation is None


def test_insufficient_overlap_blocks(db_session, tmp_path) -> None:
    """499 common days is not 'uncorrelated', it is 'unknown'."""
    store = PnLStore(tmp_path / "pnl")
    port = Alpha(
        expression="rank(ts_delta(close, 5))",
        expression_hash="overlap_port",
        status=AlphaStatus.SUBMITTED.value,
        platform_outcome=PlatformOutcome.SUBMITTED.value,
        is_valid=True,
    )
    cand = Alpha(
        expression="rank(ts_delta(volume, 5))",
        expression_hash="overlap_cand",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add_all([port, cand])
    db_session.flush()
    dates = [f"d_{i:04d}" for i in range(499)]
    store.save_pnl(port.id, dates, np.ones(499))
    store.save_pnl(cand.id, dates, np.ones(499))

    v = check_portfolio_empirical_correlation(
        db_session, cand.id, pnl_store=store, portfolio=[port], min_overlap=500
    )
    assert v.blocking is True
    assert v.method == "unmeasured"
    assert v.skipped_pairs == 1


def test_empty_portfolio_does_not_block(db_session, tmp_path) -> None:
    """Nothing to collide with is a real pass, not an unmeasured one."""
    store = PnLStore(tmp_path / "pnl")
    cand = Alpha(
        expression="rank(ts_delta(volume, 5))",
        expression_hash="empty_cand",
        status=AlphaStatus.TESTING.value,
        is_valid=True,
    )
    db_session.add(cand)
    db_session.flush()
    v = check_portfolio_empirical_correlation(db_session, cand.id, pnl_store=store, portfolio=[])
    assert v.blocking is False
    assert v.method == "none"


def test_allow_unmeasured_escape_hatch_is_not_used_by_the_gate() -> None:
    """Structural: plateau.evaluate must never pass allow_unmeasured."""
    src = (Path(__file__).parents[1] / "app/services/plateau.py").read_text(encoding="utf-8")
    assert "allow_unmeasured" not in src


def test_correlation_matrix_vectorization() -> None:
    mat = np.array(
        [
            [1.0, 2.0, 3.0, 4.0, 5.0] * 10,
            [5.0, 4.0, 3.0, 2.0, 1.0] * 10,
            [1.0, 0.0, 1.0, 0.0, 1.0] * 10,
        ]
    )
    res = compute_correlation_matrix(mat)
    assert res.shape == (3, 3)
    assert np.allclose(np.diag(res), 1.0)
    assert abs(res[0, 1] - (-1.0)) < 1e-4
