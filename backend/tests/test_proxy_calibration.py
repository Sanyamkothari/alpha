"""Phase 0 tests — Proxy calibration and structural degenerate signal detection."""

from __future__ import annotations

from app.models.alphas import Alpha
from app.models.results import AlphaMetric, SimulationImport
from app.services.proxy_calibration import (
    calibrate_proxy_rankings,
    is_degenerate_signal,
)
from app.validator.parser import parse


def test_detects_trivial_arithmetic_degeneracies() -> None:
    # subtract(x, x)
    ast1 = parse("subtract(close, close)")
    res1 = is_degenerate_signal(ast1)
    assert res1.is_degenerate
    assert "subtract(x, x)" in (res1.reason or "")

    # divide(x, x)
    ast2 = parse("divide(volume, volume)")
    res2 = is_degenerate_signal(ast2)
    assert res2.is_degenerate
    assert "divide(x, x)" in (res2.reason or "")

    # multiply(x, 0)
    ast3 = parse("multiply(close, 0)")
    res3 = is_degenerate_signal(ast3)
    assert res3.is_degenerate
    assert "multiply(x, 0)" in (res3.reason or "")


def test_detects_idempotent_redundancies() -> None:
    # rank(rank(x))
    ast1 = parse("rank(rank(close))")
    res1 = is_degenerate_signal(ast1)
    assert res1.is_degenerate
    assert "rank(rank" in (res1.reason or "")

    # zscore(zscore(x))
    ast2 = parse("zscore(zscore(volume))")
    res2 = is_degenerate_signal(ast2)
    assert res2.is_degenerate
    assert "zscore(zscore" in (res2.reason or "")

    # ts_rank(ts_rank(x, 20), 20)
    ast3 = parse("ts_rank(ts_rank(close, 20), 20)")
    res3 = is_degenerate_signal(ast3)
    assert res3.is_degenerate
    assert "ts_rank(ts_rank" in (res3.reason or "")


def test_detects_nested_window_inversions() -> None:
    # ts_delta(ts_rank(close, 20), 5) -> inner (20) >= outer (5) in acceleration
    ast1 = parse("ts_delta(ts_rank(close, 20), 5)")
    res1 = is_degenerate_signal(ast1)
    assert res1.is_degenerate
    assert "inner window" in (res1.reason or "")

    # Clean depth-2: inner (5) < outer (20)
    ast2 = parse("ts_delta(ts_rank(close, 5), 20)")
    res2 = is_degenerate_signal(ast2)
    assert not res2.is_degenerate


def test_passes_sound_production_alphas() -> None:
    # Flagship liabilities/cap formula
    ast1 = parse("rank(ts_zscore(divide(ts_backfill(liabilities, 120), cap), 5))")
    res1 = is_degenerate_signal(ast1)
    assert not res1.is_degenerate

    # Clean momentum
    ast2 = parse("group_neutralize(ts_delta(close, 10), sector)")
    res2 = is_degenerate_signal(ast2)
    assert not res2.is_degenerate


def test_calibration_runner_on_database(db_session) -> None:
    # Insert test alphas and metrics
    alpha = Alpha(
        expression="rank(ts_delta(close, 5))",
        expression_hash="calib_h1",
        status="passed",
        is_valid=True,
        complexity_score=1.5,
        feature_json={"expected_turnover_proxy": 0.45},
    )
    db_session.add(alpha)
    db_session.flush()

    sim_imp = SimulationImport(alpha_id=alpha.id, source="brain_api", raw_payload={})
    db_session.add(sim_imp)
    db_session.flush()

    metric = AlphaMetric(
        simulation_import_id=sim_imp.id,
        alpha_id=alpha.id,
        sharpe=1.85,
        fitness=1.20,
        turnover=0.42,
        passed_all_checks=True,
    )
    db_session.add(metric)
    db_session.commit()

    report = calibrate_proxy_rankings(db_session, sample_size=10)
    assert report.sample_size >= 1
    assert report.is_proxy_viable is False  # requires >= 10 samples for viability
