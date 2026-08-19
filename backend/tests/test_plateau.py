"""The filter is the product (STRATEGY.md Rule 5).

Mass simulation without an honest filter produces confident garbage faster, so
these tests pin the two behaviours everything else depends on: a lone spike is
rejected, and a broad ridge is promoted. The spike case is the one that matters
— it is the failure mode that would put overfit noise in front of the operator.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.results import AlphaMetric, SimulationImport
from app.services.filter_config import DEFAULT_FILTER_CONFIG, TRADING_DAYS_PER_YEAR
from app.services.plateau import (
    BASE_SHARPE_BAR,
    DECAY_LADDER,
    WINDOW_LADDER,
    evaluate,
    haircut_bar,
)
from app.services.pnl_storage import PnLStore
from app.services.trials import TrialLedger


def _fixed_ledger(n_trials: int = 24) -> TrialLedger:
    """A trial universe the test controls.

    The promotion bar deflates for every trial the programme has ever run, so an
    evaluate() that builds its own ledger reads the whole shared test database
    and a shape test starts depending on how many alphas unrelated tests inserted
    before it. These tests are about surface shape, not about multiple testing;
    they pin the bar so the thing under test is the only thing that moves.
    """
    return TrialLedger(
        n_trials=n_trials,
        n_eff=float(n_trials),
        sigma_sr_daily=0.35 / math.sqrt(252),
        window_days=1236,
    )


_STRUCTURE = {"ts": "ts_zscore", "cs": "rank", "group": None, "truncation": 0.08}


def _point(
    db,
    family: str,
    window: int,
    decay: int,
    sharpe: float,
    *,
    passes: bool,
    pnl_store: PnLStore | None = None,
    n_days: int = 1236,
) -> Alpha:
    """Insert one simulated grid point and store reconciled daily PnL."""
    fcode = family.replace("/", "_").replace("@", "_").replace(":", "_")
    grid = dict(_STRUCTURE, window=window, decay=decay, neutralization="SUBINDUSTRY", field=fcode)
    alpha = Alpha(
        expression=f"rank(ts_zscore({fcode},{window}))",
        normalized_expression=f"rank(ts_zscore({fcode},{window}))",
        expression_hash=f"{family}-{window}-{decay}",
        family_key=family,
        status="rejected",
        source="constructor",
        region="USA",
        universe="TOP3000",
        delay=1,
        neutralization="SUBINDUSTRY",
        decay=decay,
        truncation=0.08,
        is_valid=True,
        generation=0,
        feature_json={"grid": grid},
    )
    db.add(alpha)
    db.flush()
    imp = SimulationImport(alpha_id=alpha.id, source="brain_api", raw_payload={})
    db.add(imp)
    db.flush()
    db.add(
        AlphaMetric(
            alpha_id=alpha.id,
            simulation_import_id=imp.id,
            sharpe=sharpe,
            fitness=1.5,
            turnover=0.3,
            passed_all_checks=passes,
        )
    )
    db.flush()

    if pnl_store is not None:
        dates = [f"d_{i:04d}" for i in range(n_days)]
        daily_sharpe = sharpe / math.sqrt(TRADING_DAYS_PER_YEAR)
        daily_vol = 0.01
        rng = np.random.default_rng(hash((family, window, decay)) % (2**31))
        pnl = rng.normal(daily_sharpe * daily_vol, daily_vol, n_days)
        # Ensure sample Sharpe matches reported Sharpe within tolerance
        std = float(np.std(pnl, ddof=1))
        if std > 0:
            target_mean = daily_sharpe * std
            pnl = pnl - np.mean(pnl) + target_mean
        pnl_store.save_pnl(alpha.id, dates, pnl)

    return alpha


def test_isolated_spike_is_not_promoted(db_session, tmp_path) -> None:
    """One brilliant point surrounded by dead neighbours is luck, not signal."""
    fam = "spike/test"
    store = PnLStore(tmp_path / "pnl")
    target_w, target_d = WINDOW_LADDER[2], DECAY_LADDER[3]
    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            sharpe = 9.0 if (w, d) == (target_w, target_d) else 0.05
            _point(db_session, fam, w, d, sharpe, passes=(w, d) == (target_w, target_d), pnl_store=store)

    verdicts = {v.alpha_id: v for v in evaluate(db_session, fam, pnl_store=store, ledger=_fixed_ledger())}
    spike = next(v for v in verdicts.values() if v.sharpe == 9.0)
    assert spike.clears_bar, "precondition: BRAIN checks passed"
    assert not spike.is_plateau, "a lone spike must not read as a plateau"
    assert not spike.promoted, "a spike must never reach the operator"
    assert any("spike" in r for r in spike.reasons)


def test_broad_plateau_is_promoted(db_session, tmp_path) -> None:
    """A ridge whose neighbours also score well is a mechanism."""
    fam = "plateau/test"
    store = PnLStore(tmp_path / "pnl")
    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            _point(db_session, fam, w, d, 2.5, passes=True, pnl_store=store)

    verdicts = evaluate(db_session, fam, pnl_store=store, ledger=_fixed_ledger())
    assert any(v.promoted for v in verdicts), "a uniform high surface must promote"
    best = next(v for v in verdicts if v.promoted)
    assert best.is_plateau
    assert best.neighbour_median_sharpe == 2.5


def test_failing_brain_checks_blocks_promotion(db_session, tmp_path) -> None:
    """The plateau test never overrides the platform's own bar."""
    fam = "nochecks/test"
    store = PnLStore(tmp_path / "pnl")
    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            _point(db_session, fam, w, d, 3.0, passes=False, pnl_store=store)

    verdicts = evaluate(db_session, fam, pnl_store=store, ledger=_fixed_ledger())
    assert not any(v.promoted for v in verdicts)
    assert all("fails BRAIN checks" in v.reasons for v in verdicts)


def test_incomplete_surface_is_not_promoted(db_session, tmp_path) -> None:
    """No neighbours simulated => unjudgeable, so held back rather than promoted."""
    fam = "lonely/test"
    store = PnLStore(tmp_path / "pnl")
    _point(db_session, fam, WINDOW_LADDER[2], DECAY_LADDER[3], 5.0, passes=True, pnl_store=store)
    verdicts = evaluate(db_session, fam, pnl_store=store, ledger=_fixed_ledger())
    assert not verdicts[0].promoted
    assert any("no simulated neighbours" in r for r in verdicts[0].reasons)


def test_haircut_grows_with_family_size(db_session) -> None:
    """A winner drawn from 2,000 candidates needs a higher bar than one from 20."""
    assert haircut_bar(1) == BASE_SHARPE_BAR
    assert haircut_bar(20) > BASE_SHARPE_BAR
    assert haircut_bar(2000) > haircut_bar(20)


def test_portfolio_correlation_blocks_promotion(db_session, tmp_path) -> None:
    """An alpha colliding structurally with an already-submitted alpha is blocked."""
    fam = "close_sub_test/cap@USA/TOP3000/d1"
    store = PnLStore(tmp_path / "pnl")
    target_w, target_d = WINDOW_LADDER[2], DECAY_LADDER[3]
    submitted_alpha = Alpha(
        expression=f"rank(ts_zscore(close_sub_test,{target_w}))",
        normalized_expression=f"rank(ts_zscore(close_sub_test,{target_w}))",
        expression_hash="submitted-hash-1",
        family_key=fam,
        status="submitted",
        source="constructor",
        region="USA",
        universe="TOP3000",
        delay=1,
        neutralization="SUBINDUSTRY",
        decay=target_d,
        truncation=0.08,
        is_valid=True,
        generation=0,
        feature_json={"structural_hash": "shash-close_sub_test-zscore", "grid": {"window": target_w, "decay": target_d}},
    )
    db_session.add(submitted_alpha)
    db_session.flush()
    db_session.add(SubmissionAttempt(alpha_id=submitted_alpha.id, result="submitted"))
    db_session.flush()

    dates = [f"d_{i:04d}" for i in range(1236)]

    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            a = _point(db_session, fam, w, d, 2.5, passes=True, pnl_store=store)
            a.feature_json = {
                "structural_hash": "shash-close_sub_test-zscore",
                "grid": dict(_STRUCTURE, window=w, decay=d, neutralization="SUBINDUSTRY", field="close_sub_test"),
            }
    db_session.flush()

    verdicts = evaluate(db_session, fam, pnl_store=store, ledger=_fixed_ledger())
    assert not any(v.promoted for v in verdicts), "colliding family alphas must not be promoted"
    assert any(v.is_correlated for v in verdicts)
    assert any("collision with submitted alpha" in r or "submitted alpha" in r for v in verdicts for r in v.reasons)


def test_neighbours_derives_ladders_from_surface_points() -> None:
    """_neighbours dynamically resolves coordinates from the surface points, not stale constants."""
    from app.services.plateau import SurfacePoint, _neighbours

    custom_windows = [5, 10, 20, 40, 60, 120, 250]
    custom_decays = [0, 1, 2, 4, 6, 8, 16]

    surface = [
        SurfacePoint(
            alpha_id=i,
            expression=f"e_{w}_{d}",
            window=w,
            decay=d,
            sharpe=1.5,
            fitness=1.0,
            turnover=0.2,
            passed_all_checks=True,
            structure=("ts_zscore", "rank", None, 0.08, "SUBINDUSTRY"),
        )
        for i, (w, d) in enumerate(
            (w, d) for w in custom_windows for d in custom_decays
        )
    ]

    target = next(p for p in surface if p.window == 40 and p.decay == 4)
    neighbours, possible = _neighbours(target, surface)

    assert possible == 4  # 2 window neighbours (20, 60) + 2 decay neighbours (2, 6)
    assert len(neighbours) == 4
    neigh_coords = {(n.window, n.decay) for n in neighbours}
    expected_coords = {(20, 4), (60, 4), (40, 2), (40, 6)}
    assert neigh_coords == expected_coords
