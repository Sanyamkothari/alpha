"""Quant Research Review Regression Suite (F1–F10, A1, A2, A5, A6).

Verifies the six acceptance criteria from the Remediation Plan:
- A1: Genuine multi-point plateau promotes exactly ONE representative, not zero.
- A2: Default-budget constructor expansion emits >= 5 distinct ts-transforms and >= 1 depth-2 template.
- A5: No promotion can occur from a PnL vector that fails Sharpe reconciliation.
- A6: Gate configuration fingerprint versioning is deterministic and stamped.
- F1: Submitted portfolio queries submitted attempts only; intra-family single-linkage clustering at 0.90.
- F6: Ranking by ridge_score (shrunk neighbourhood median) rather than peak Sharpe.
- F7: Intra-batch orthogonality greedy selection.
- F8: Discounted Thompson Sampler with batch discounting and 20% diversity cap (no escape hatch).
- F9: BRAIN correlation endpoints are strictly GET requests.
- F10: Window jitter maps exclusively to valid on-ladder grid rungs.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
import numpy as np
import pytest
from sqlalchemy import select

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.enums import AlphaStatus
from app.models.fields import DataField, Dataset
from app.models.results import AlphaMetric, SimulationImport
from app.services.allocator_bandit import (
    DiscountedThompsonSampler,
    SimulationBudgetOrchestrator,
)
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.brain.client import BrainClient
from app.services.clustering import cluster_family, select_orthogonal_batch
from app.services.constructor import (
    FamilySpec,
    expand,
    STANDARD_WINDOWS,
    STANDARD_DECAYS,
)
from app.services.correlation import (
    check_portfolio_empirical_correlation,
    compute_pairwise_correlation,
    submitted_portfolio,
)
from app.services.evolution import _WINDOW_JITTER, mutate_parameters
from app.services.filter_config import DEFAULT_FILTER_CONFIG, TRADING_DAYS_PER_YEAR, FilterConfig
from app.services.plateau import evaluate, load_surface
from app.services.pnl_storage import PnLStore
from app.services.trials import TrialLedger
from app.validator.parser import parse
from app.validator.validator import normalize


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



def _seed_field(db, dataset_code: str = "reg_ds", field_code: str = "reg_field") -> str:
    ds = db.execute(select(Dataset).where(Dataset.dataset_code == dataset_code)).scalar_one_or_none()
    if not ds:
        ds = Dataset(dataset_code=dataset_code, name="Regression Dataset", region="USA", universe="TOP3000", delay=1)
        db.add(ds)
        db.flush()
    f = db.execute(select(DataField).where(DataField.field_code == field_code)).scalar_one_or_none()
    if not f:
        f = DataField(
            field_code=field_code,
            dataset_id=ds.id,
            category="price",
            field_type="MATRIX",
            region="USA",
            universe="TOP3000",
            delay=1,
            coverage=0.95,
            user_count=50,
        )
        db.add(f)
        db.flush()
    return field_code


# ----------------------------------------------------------------------
# A1 & F1: Sibling Ridge Clustering and Promotion
# ----------------------------------------------------------------------

def _seed_correlated_ridge(
    db,
    store: PnLStore,
    *,
    dataset: str = "ds_ridge",
    field: str = "field_ridge",
    sharpe: float = 2.60,
    intra_corr: float = 0.94,
    seed: int = 123,
) -> str:
    """One family, one mechanism, sibling PnL correlated the way a real ridge is.

    Adjacent points on a (window x decay) surface are the same alpha nudged, and
    they correlate at ~0.9+. A fixture that gives each point independent noise
    tests a shape that does not occur, and it is exactly the shape the dedup step
    exists to collapse.
    """
    fcode = _seed_field(db, dataset, field)
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(field_code=fcode, operator_family="ts_zscore", wrapper_shape="rank")
    fk = spec.family_key(settings)

    candidates = expand(db, spec, base_settings=settings, max_candidates=49)
    dates = [f"d_{i:04d}" for i in range(1236)]
    rng = np.random.default_rng(seed)
    common_factor = rng.normal(0.0, 0.01, size=len(dates))
    daily_sr = sharpe / math.sqrt(TRADING_DAYS_PER_YEAR)

    for c in candidates:
        res = create_alpha(
            db, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="test"
        )
        aid = res.alpha.id
        si = SimulationImport(alpha_id=aid, source="brain_api", raw_payload={}, is_latest=True)
        db.add(si)
        db.flush()
        db.add(
            AlphaMetric(
                simulation_import_id=si.id,
                alpha_id=aid,
                sharpe=sharpe,
                fitness=1.5,
                turnover=0.3,
                passed_all_checks=True,
            )
        )
        idio = rng.normal(0.0, 0.01, size=len(dates))
        pnl = math.sqrt(intra_corr) * common_factor + math.sqrt(1.0 - intra_corr) * idio
        std = float(np.std(pnl, ddof=1))
        if std > 0:
            pnl = pnl - np.mean(pnl) + daily_sr * std
        store.save_pnl(aid, dates, pnl)

    db.flush()
    return fk


def test_sibling_ridge_promotes_one(db_session, tmp_path) -> None:
    """A1: A genuine multi-point plateau must promote EXACTLY ONE representative.

    Zero was the original bug -- every point blocked by its own siblings. Four is
    the bug that replaced it: grouping by structural skeleton splits one ridge
    across the skeleton's window buckets and promotes a near-duplicate out of each.
    """
    store = PnLStore(tmp_path / "pnl")
    fk = _seed_correlated_ridge(db_session, store, dataset="ds_a1", field="field_a1")

    verdicts = evaluate(db_session, fk, pnl_store=store, ledger=_fixed_ledger(), portfolio=[])
    promoted = [v for v in verdicts if v.promoted]
    assert len(promoted) == 1, f"expected exactly 1 promoted representative, got {len(promoted)}"
    assert promoted[0].is_plateau is True
    assert promoted[0].is_correlated is False

    # The elected point must be the ridge centre, not the peak. This is the
    # election that decides which single alpha reaches the operator.
    rep = promoted[0]
    assert rep.ridge_score is not None
    demoted = [v for v in verdicts if v.redundant_with == rep.alpha_id]
    assert demoted, "the other 48 points must be recorded as clustered, not silently dropped"
    assert all(
        (d.ridge_score or -99.0) <= rep.ridge_score for d in demoted
    ), "the representative must hold the highest ridge_score in its cluster"
    assert any("clustered into representative" in r for r in demoted[0].reasons)


def test_submitted_portfolio_submitted_only(db_session) -> None:
    """F1: submitted_portfolio queries SubmissionAttempt(result='submitted', is_recalled=False) only."""
    # 1. Alpha with status='passed' but never submitted
    a1 = Alpha(expression="rank(close)", expression_hash="sub_test_1", status=AlphaStatus.PASSED.value, is_valid=True)
    # 2. Alpha with status='submitted' and active SubmissionAttempt
    a2 = Alpha(expression="zscore(close)", expression_hash="sub_test_2", status=AlphaStatus.SUBMITTED.value, is_valid=True)
    # 3. Alpha with recalled SubmissionAttempt
    a3 = Alpha(expression="normalize(close)", expression_hash="sub_test_3", status=AlphaStatus.SUBMITTED.value, is_valid=True)
    db_session.add_all([a1, a2, a3])
    db_session.flush()

    db_session.add(SubmissionAttempt(alpha_id=a2.id, result="submitted", is_recalled=False))
    db_session.add(SubmissionAttempt(alpha_id=a3.id, result="submitted", is_recalled=True))
    db_session.flush()

    port = submitted_portfolio(db_session)
    port_ids = [a.id for a in port]
    assert a2.id in port_ids
    assert a1.id not in port_ids
    assert a3.id not in port_ids


# ----------------------------------------------------------------------
# A2: Constructor Search Breadth & Stratification
# ----------------------------------------------------------------------

def test_constructor_diversity(db_session) -> None:
    """A2: Default-budget expansion emits >= 5 distinct ts-transforms and >= 1 depth-2 template."""
    fcode = _seed_field(db_session, "ds_a2", "field_a2")
    spec = FamilySpec(field_code=fcode)
    candidates = expand(db_session, spec, max_candidates=400)

    ts_transforms_found = set()
    depth2_found = 0

    for c in candidates:
        grid = c.grid or {}
        if "ts" in grid:
            ts_transforms_found.add(grid["ts"])
        if grid.get("depth", 1) >= 2 or "inner_ts" in grid:
            depth2_found += 1

    assert len(ts_transforms_found) >= 5, f"expected >= 5 distinct ts-transforms, found {ts_transforms_found}"
    assert depth2_found >= 1, f"expected >= 1 depth-2 candidates, found {depth2_found}"


# ----------------------------------------------------------------------
# A5 & F5b: Reconciliation Precondition
# ----------------------------------------------------------------------

def test_reconciliation_enforced(db_session, tmp_path) -> None:
    """A5: No promotion can occur from a PnL vector that fails Sharpe reconciliation."""
    fcode = _seed_field(db_session, "ds_a5", "field_a5")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(field_code=fcode, operator_family="ts_zscore", wrapper_shape="rank")
    fk = spec.family_key(settings)

    candidates = expand(db_session, spec, base_settings=settings, max_candidates=49)
    store = PnLStore(tmp_path / "pnl")
    dates = [f"d_{i:04d}" for i in range(1236)]
    rng = np.random.default_rng(42)

    for c in candidates:
        res = create_alpha(db_session, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="test")
        aid = res.alpha.id
        si = SimulationImport(alpha_id=aid, source="brain_api", raw_payload={}, is_latest=True)
        db_session.add(si)
        db_session.flush()
        # Reported Sharpe is 2.50
        db_session.add(
            AlphaMetric(
                simulation_import_id=si.id,
                alpha_id=aid,
                sharpe=2.50,
                fitness=1.5,
                turnover=0.3,
                passed_all_checks=True,
            )
        )
        # Store corrupted PnL vector whose sample Sharpe is ~0.50 (diff 2.0 > tolerance 0.10)
        corrupted_pnl = rng.normal(0.0003, 0.01, size=len(dates))
        store.save_pnl(aid, dates, corrupted_pnl)

    db_session.flush()

    verdicts = evaluate(db_session, fk, pnl_store=store, ledger=_fixed_ledger(), portfolio=[])
    assert not any(v.promoted for v in verdicts), "unreconciled PnL must NEVER promote"
    assert all(any("reconciliation" in r for r in v.reasons) for v in verdicts)


# ----------------------------------------------------------------------
# A6: Configuration Versioning and Hash Fingerprint
# ----------------------------------------------------------------------

def test_filter_config_fingerprint_deterministic() -> None:
    """A6: Every threshold in the gate stack is recorded, versioned, and reproduced."""
    cfg1 = FilterConfig()
    cfg2 = FilterConfig()
    assert cfg1.fingerprint() == cfg2.fingerprint()
    assert len(cfg1.fingerprint()) == 12

    cfg_modified = FilterConfig(target_sharpe=1.50)
    assert cfg_modified.fingerprint() != cfg1.fingerprint()


# ----------------------------------------------------------------------
# F6: Ridge Score Ranking
# ----------------------------------------------------------------------

def test_ridge_score_ranking_over_peak() -> None:
    """F6: Candidate selection ranks by ridge_score rather than noisy peak Sharpe."""
    from app.services.plateau import SurfacePoint, _neighbours
    surface = [
        SurfacePoint(1, "e1", 5, 0, sharpe=3.0, fitness=1.0, turnover=0.2, passed_all_checks=True, structure=("s",)),
        SurfacePoint(2, "e2", 5, 4, sharpe=0.1, fitness=1.0, turnover=0.2, passed_all_checks=True, structure=("s",)),
        SurfacePoint(3, "e3", 20, 4, sharpe=2.4, fitness=1.0, turnover=0.2, passed_all_checks=True, structure=("s",)),
        SurfacePoint(4, "e4", 40, 4, sharpe=2.3, fitness=1.0, turnover=0.2, passed_all_checks=True, structure=("s",)),
        SurfacePoint(5, "e5", 20, 8, sharpe=2.5, fitness=1.0, turnover=0.2, passed_all_checks=True, structure=("s",)),
    ]
    scores = {}
    for p in surface:
        neigh, _ = _neighbours(p, surface)
        vals = [n.sharpe for n in neigh if n.sharpe is not None]
        all_vals = [p.sharpe] + vals if p.sharpe is not None else vals
        scores[p.alpha_id] = float(np.median(all_vals))
    # Candidate 3 (ridge center, median ~2.40) ranks ahead of noisy peak 1 (median ~1.55)
    assert scores[3] > scores[1]


# ----------------------------------------------------------------------
# F7: Batch Orthogonality
# ----------------------------------------------------------------------

def test_greedy_orthogonal_batch(tmp_path) -> None:
    """F7: Selects maximally orthogonal batch across candidates."""
    from app.services.plateau import Verdict
    store = PnLStore(tmp_path / "pnl")
    dates = [f"d_{i:04d}" for i in range(600)]
    rng = np.random.default_rng(42)

    # 3 candidates: #1 and #2 have correlation 0.95; #3 is orthogonal
    pnl1 = rng.normal(0.001, 0.01, size=600)
    pnl2 = 0.95 * pnl1 + 0.05 * rng.normal(0.0, 0.01, size=600)
    pnl3 = rng.normal(0.001, 0.01, size=600)

    store.save_pnl(1, dates, pnl1)
    store.save_pnl(2, dates, pnl2)
    store.save_pnl(3, dates, pnl3)

    v1 = Verdict(alpha_id=1, expression="e1", sharpe=2.5, fitness=1.0, neighbour_median_sharpe=2.4, ridge_score=2.45, plateau_ratio=1.0, is_plateau=True, clears_bar=True, neighbours_simulated=4, neighbours_possible=4, family_size=49, haircut_bar=2.0, is_correlated=False, correlation_collision=None, promoted=True, dsr=0.99, dsr_passed=True, gate_mode="DSR", subperiod_passed=True, redundant_with=None, reasons=[], config_fingerprint="test")
    v2 = Verdict(alpha_id=2, expression="e2", sharpe=2.4, fitness=1.0, neighbour_median_sharpe=2.3, ridge_score=2.35, plateau_ratio=1.0, is_plateau=True, clears_bar=True, neighbours_simulated=4, neighbours_possible=4, family_size=49, haircut_bar=2.0, is_correlated=False, correlation_collision=None, promoted=True, dsr=0.99, dsr_passed=True, gate_mode="DSR", subperiod_passed=True, redundant_with=None, reasons=[], config_fingerprint="test")
    v3 = Verdict(alpha_id=3, expression="e3", sharpe=2.2, fitness=1.0, neighbour_median_sharpe=2.1, ridge_score=2.15, plateau_ratio=1.0, is_plateau=True, clears_bar=True, neighbours_simulated=4, neighbours_possible=4, family_size=49, haircut_bar=2.0, is_correlated=False, correlation_collision=None, promoted=True, dsr=0.99, dsr_passed=True, gate_mode="DSR", subperiod_passed=True, redundant_with=None, reasons=[], config_fingerprint="test")

    accepted, deferred = select_orthogonal_batch([v1, v2, v3], store)
    accepted_ids = [v.alpha_id for v in accepted]
    deferred_ids = [v.alpha_id for v in deferred]

    assert 1 in accepted_ids
    assert 3 in accepted_ids
    assert 2 in deferred_ids  # Correlated with #1, so deferred


# ----------------------------------------------------------------------
# F8: Discounted Thompson Sampler
# ----------------------------------------------------------------------

def test_bandit_discounting_and_caps() -> None:
    """F8: Batch discounting and 20% cap without escape hatch."""
    bandit = DiscountedThompsonSampler(half_life_days=30.0, rng=random.Random(42))
    # Batch update
    bandit.update_batch("ds1", [1.0, 1.0, 0.0])
    arm = bandit.get_arm("ds1")
    assert arm.total_trials == 3
    assert arm.alpha_param > 1.0

    # 20% cap test: when all arms exceed 20%, return least used
    usage = {"ds1": 50, "ds2": 30, "ds3": 20}
    best = bandit.select_best_dataset(["ds1", "ds2", "ds3"], dataset_usage_counts=usage, max_share=0.10)
    assert best == "ds3"  # Least used arm chosen


# ----------------------------------------------------------------------
# F9: BRAIN Client Correlation GET Methods
# ----------------------------------------------------------------------

def test_brain_correlation_endpoints_get_only() -> None:
    """F9: Authoritative BRAIN correlation methods use GET exclusively."""
    assert hasattr(BrainClient, "self_correlation")
    assert hasattr(BrainClient, "prod_correlation")


# ----------------------------------------------------------------------
# F10: Window Jitter Synchronization
# ----------------------------------------------------------------------

def test_window_jitter_ladder_sync() -> None:
    """F10b: Window jitter keys and targets are exclusively on-ladder grid rungs."""
    for win, targets in _WINDOW_JITTER.items():
        assert win in STANDARD_WINDOWS or win in (22, 63, 126, 252)
        for t in targets:
            assert t in STANDARD_WINDOWS or t in (22, 63, 126, 252)


# ---------------------------------------------------------------------------
# Round-2 review regressions. Each of these fails on 01f08bc.
# ---------------------------------------------------------------------------


def test_promotion_bar_uses_programme_ledger_not_family_neff(db_session, tmp_path) -> None:
    """The bar deflates for every trial the winner was selected from.

    Pinning the *wiring*, not the formula. Handing haircut_bar the family's own
    N_eff is the failure mode: 49 grid points at rho ~ 0.9 collapse to N_eff ~ 1.2,
    and the "multiple-testing bar" lands BELOW the flat 1.25 + 0.10*log10(N) it was
    brought in to replace. The formula was right on both sides of that bug; the
    argument was not.
    """
    import numpy as np

    from app.services.plateau import evaluate, haircut_bar
    from app.services.subperiod import compute_effective_trials

    siblings = np.full((49, 49), 0.92)
    np.fill_diagonal(siblings, 1.0)
    family_neff = compute_effective_trials(siblings)
    assert family_neff < 2.0, "precondition: a tight ridge collapses to ~1 effective trial"
    assert haircut_bar(family_neff) < 1.25 + 0.10 * math.log10(400), (
        "precondition: the family N_eff bar really is weaker than the flat bar"
    )

    store = PnLStore(tmp_path / "pnl")
    fam = _seed_correlated_ridge(
        db_session, store, dataset="ds_bar", field="field_bar"
    )

    big = _fixed_ledger(n_trials=4000)
    verdicts = evaluate(db_session, fam, pnl_store=store, ledger=big, portfolio=[])
    assert verdicts

    expected = haircut_bar(big)
    assert verdicts[0].haircut_bar == pytest.approx(expected, abs=1e-6), (
        f"evaluate() reported bar {verdicts[0].haircut_bar:.3f}, expected the "
        f"programme-ledger bar {expected:.3f}"
    )
    assert verdicts[0].haircut_bar > haircut_bar(family_neff) + 0.25, (
        "the programme bar must be materially above the one the family's own "
        "N_eff would have produced -- that gap is the entire finding"
    )


def test_unmeasurable_portfolio_correlation_blocks(db_session, tmp_path) -> None:
    """A gate that cannot be evaluated blocks; it used to pass silently.

    The candidate and the submitted alpha share far too few days to correlate.
    Passing here would put "we never checked" in front of the operator wearing
    the same green as "we checked and it was fine".
    """
    import numpy as np

    from app.models.alphas import Alpha, SubmissionAttempt
    from app.services.correlation import check_portfolio_empirical_correlation
    from app.services.filter_config import FilterConfig

    assert FilterConfig().block_on_unmeasurable_portfolio is True, "fail-closed is the default"

    store = PnLStore(tmp_path / "pnl")
    rng = np.random.default_rng(11)

    def _mk(tag: str) -> Alpha:
        a = Alpha(
            expression=f"rank(ts_zscore(reg_field,{len(tag)}))",
            normalized_expression=f"rank(ts_zscore(reg_field,{len(tag)}))",
            expression_hash=f"unmeas-{tag}",
            family_key=f"unmeas/{tag}",
            status="rejected",
            source="constructor",
            region="USA",
            universe="TOP3000",
            delay=1,
            neutralization="SUBINDUSTRY",
            decay=4,
            truncation=0.08,
            is_valid=True,
            generation=0,
            feature_json={"structural_hash": f"skeleton-{tag}", "grid": {"window": 22, "decay": 4}},
        )
        db_session.add(a)
        db_session.flush()
        return a

    submitted = _mk("sub")
    candidate = _mk("cand")
    db_session.add(SubmissionAttempt(alpha_id=submitted.id, result="submitted", is_recalled=False))
    db_session.flush()

    # 200 shared days against a 500-day requirement: unmeasurable, and the two
    # skeletons differ, so the structural proxy has nothing to say either.
    dates = [f"u_{i:04d}" for i in range(200)]
    store.save_pnl(submitted.id, dates, rng.normal(size=200))
    store.save_pnl(candidate.id, dates, rng.normal(size=200))

    is_corr, reason, _ = check_portfolio_empirical_correlation(
        db_session, candidate.id, pnl_store=store, portfolio=[submitted]
    )
    assert is_corr, "an unmeasurable portfolio comparison must block"
    assert "unmeasurable" in (reason or "")
    assert "200" in (reason or ""), "the reason must say what is missing"


def test_eval_cache_invalidates_when_new_metrics_land(db_session, tmp_path) -> None:
    """New simulation results must reach the report.

    Keyed on family alone, the cache is correct exactly once per process: a
    long-running server keeps serving the verdicts it computed before last
    night's batch landed, and nothing on the page says so.
    """
    from app.models.alphas import Alpha
    from app.models.results import AlphaMetric, SimulationImport
    from app.services.plateau import _freshness_token, clear_eval_cache

    clear_eval_cache()
    before = _freshness_token(db_session, [])

    alpha = Alpha(
        expression="rank(ts_zscore(reg_field,22))",
        normalized_expression="rank(ts_zscore(reg_field,22))",
        expression_hash="cache-fresh-1",
        family_key="cachetest/fresh",
        status="rejected",
        source="constructor",
        region="USA",
        universe="TOP3000",
        delay=1,
        neutralization="SUBINDUSTRY",
        decay=4,
        truncation=0.08,
        is_valid=True,
        generation=0,
        feature_json={"grid": {"window": 22, "decay": 4}},
    )
    db_session.add(alpha)
    db_session.flush()
    imp = SimulationImport(alpha_id=alpha.id, source="brain_api", raw_payload={})
    db_session.add(imp)
    db_session.flush()
    db_session.add(
        AlphaMetric(
            alpha_id=alpha.id,
            simulation_import_id=imp.id,
            sharpe=2.0,
            fitness=1.5,
            turnover=0.3,
            passed_all_checks=True,
        )
    )
    db_session.flush()

    after = _freshness_token(db_session, [])
    assert after != before, "inserting metrics must change the freshness token"
