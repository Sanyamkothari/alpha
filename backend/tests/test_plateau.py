"""The filter is the product (STRATEGY.md Rule 5).

Mass simulation without an honest filter produces confident garbage faster, so
these tests pin the two behaviours everything else depends on: a lone spike is
rejected, and a broad ridge is promoted. The spike case is the one that matters
— it is the failure mode that would put overfit noise in front of the operator.
"""

from __future__ import annotations

from app.models.alphas import Alpha
from app.models.results import AlphaMetric, SimulationImport
from app.services.plateau import (
    BASE_SHARPE_BAR,
    DECAY_LADDER,
    WINDOW_LADDER,
    evaluate,
    haircut_bar,
)

_STRUCTURE = {"ts": "ts_zscore", "cs": "rank", "group": None, "truncation": 0.08}


def _point(db, family: str, window: int, decay: int, sharpe: float, *, passes: bool) -> Alpha:
    """Insert one simulated grid point."""
    grid = dict(_STRUCTURE, window=window, decay=decay, neutralization="SUBINDUSTRY")
    alpha = Alpha(
        expression=f"rank(ts_zscore(close,{window}))",
        normalized_expression=f"rank(ts_zscore(close,{window}))",
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
    return alpha


def test_isolated_spike_is_not_promoted(db_session) -> None:
    """One brilliant point surrounded by dead neighbours is luck, not signal."""
    fam = "spike/test"
    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            sharpe = 9.0 if (w, d) == (22, 4) else 0.05
            _point(db_session, fam, w, d, sharpe, passes=(w, d) == (22, 4))

    verdicts = {v.alpha_id: v for v in evaluate(db_session, fam, require_pnl=False)}
    spike = next(v for v in verdicts.values() if v.sharpe == 9.0)
    assert spike.clears_bar, "precondition: BRAIN checks passed"
    assert not spike.is_plateau, "a lone spike must not read as a plateau"
    assert not spike.promoted, "a spike must never reach the operator"
    assert any("spike" in r for r in spike.reasons)


def test_broad_plateau_is_promoted(db_session) -> None:
    """A ridge whose neighbours also score well is a mechanism."""
    fam = "plateau/test"
    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            _point(db_session, fam, w, d, 2.0, passes=True)

    verdicts = evaluate(db_session, fam, require_pnl=False)
    assert any(v.promoted for v in verdicts), "a uniform high surface must promote"
    best = next(v for v in verdicts if v.promoted)
    assert best.is_plateau
    assert best.neighbour_median_sharpe == 2.0


def test_failing_brain_checks_blocks_promotion(db_session) -> None:
    """The plateau test never overrides the platform's own bar."""
    fam = "nochecks/test"
    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            _point(db_session, fam, w, d, 3.0, passes=False)

    verdicts = evaluate(db_session, fam, require_pnl=False)
    assert not any(v.promoted for v in verdicts)
    assert all("fails BRAIN checks" in v.reasons for v in verdicts)


def test_incomplete_surface_is_not_promoted(db_session) -> None:
    """No neighbours simulated => unjudgeable, so held back rather than promoted.

    Absence of evidence is not evidence — this is the conservative direction on
    purpose.
    """
    fam = "lonely/test"
    _point(db_session, fam, 22, 4, 5.0, passes=True)
    verdicts = evaluate(db_session, fam, require_pnl=False)
    assert not verdicts[0].promoted
    assert any("no simulated neighbours" in r for r in verdicts[0].reasons)


def test_haircut_grows_with_family_size(db_session) -> None:
    """A winner drawn from 2,000 candidates needs a higher bar than one from 20."""
    assert haircut_bar(1) == BASE_SHARPE_BAR
    assert haircut_bar(20) > BASE_SHARPE_BAR
    assert haircut_bar(2000) > haircut_bar(20)


def test_portfolio_correlation_blocks_promotion(db_session) -> None:
    """An alpha colliding structurally with an already-submitted alpha is blocked."""
    submitted_alpha = Alpha(
        expression="rank(ts_zscore(close,22))",
        normalized_expression="rank(ts_zscore(close,22))",
        expression_hash="submitted-hash-1",
        family_key="close/cap@USA/TOP3000/d1",
        status="submitted",
        source="constructor",
        region="USA",
        universe="TOP3000",
        delay=1,
        neutralization="SUBINDUSTRY",
        decay=4,
        truncation=0.08,
        is_valid=True,
        generation=0,
        feature_json={"structural_hash": "shash-close-zscore", "grid": {"window": 22, "decay": 4}},
    )
    db_session.add(submitted_alpha)
    db_session.flush()

    fam = "close/cap@USA/TOP3000/d1"
    for w in WINDOW_LADDER:
        for d in DECAY_LADDER:
            a = _point(db_session, fam, w, d, 2.5, passes=True)
            a.feature_json = {
                "structural_hash": "shash-close-zscore",
                "grid": dict(_STRUCTURE, window=w, decay=d, neutralization="SUBINDUSTRY"),
            }
    db_session.flush()

    verdicts = evaluate(db_session, fam, require_pnl=False)
    assert not any(v.promoted for v in verdicts), "colliding family alphas must not be promoted"
    assert all(v.is_correlated for v in verdicts)
    assert any("collision with submitted alpha" in r for v in verdicts for r in v.reasons)
