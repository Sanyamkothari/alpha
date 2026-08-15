from app.models.alphas import Alpha, SubmissionAttempt
from app.models.campaigns import Campaign, CampaignTask
from app.models.fields import DataField, Dataset
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric
from app.services.allocator import plan_budget_allocation
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import FamilySpec, expand
from app.services.correlation import compute_max_self_correlation_with_submitted
from app.services.evolution import EvolutionConfig, evolve_generation
from app.services.pnl_storage import PnLStore
import numpy as np


def test_standard_7x7_grid_expansion(db_session):
    """Task 1: Standard grid yields exactly 49 alphas per territory."""
    spec = FamilySpec(
        field_code="close",
        operator_family="ts_zscore",
        wrapper_shape="rank",
        grid_mode="standard",
    )
    candidates = expand(db_session, spec, max_candidates=49)
    assert len(candidates) == 49
    # Check that windows and decays are 7 distinct points each
    windows = {c.grid["window"] for c in candidates}
    decays = {c.grid["decay"] for c in candidates}
    assert len(windows) == 7
    assert len(decays) == 7


def test_three_arm_budget_allocation(db_session):
    """Task 3: 3-arm budget allocation splits into exploit, random stratified, and plateau fill."""
    plan = plan_budget_allocation(db_session, total_simulations=200)
    assert plan.total_simulations == 200
    assert plan.exploit_simulations == 100
    assert plan.random_stratified_simulations == 60
    assert plan.plateau_fill_simulations == 40

    arms = {t.arm for t in plan.tasks}
    assert "exploit" in arms
    assert "random_stratified" in arms


def test_self_correlation_ground_truth(db_session, tmp_path):
    """Task 2e: Pre-submission self-correlation uses submission_attempts ground truth and never blank."""
    store = PnLStore(tmp_path / "pnl")

    # 1. Create candidate alpha
    cand = create_alpha(
        db_session,
        "rank(ts_zscore(close, 20))",
        AlphaSettings(),
        family_key="close@USA/TOP3000/d1",
    ).alpha

    # With no PnL yet, returns structural proxy or none
    corr, target_id, method = compute_max_self_correlation_with_submitted(
        db_session, cand.id, pnl_store=store
    )
    assert method in ["none", "structural_proxy"]
    assert corr is not None

    # 2. Create a submitted alpha
    sub_alpha = create_alpha(
        db_session,
        "rank(ts_zscore(volume, 20))",
        AlphaSettings(),
        family_key="volume@USA/TOP3000/d1",
    ).alpha

    # Add confirmed submission attempt
    attempt = SubmissionAttempt(
        alpha_id=sub_alpha.id,
        result="submitted",
    )
    db_session.add(attempt)
    db_session.commit()

    # Save mock PnL for both
    # Make 600 points
    dates = [f"D{i}" for i in range(600)]
    cand_pnl = np.random.normal(0, 1, 600)
    sub_pnl = cand_pnl * 0.8 + np.random.normal(0, 0.2, 600)

    store.save_pnl(cand.id, dates, cand_pnl)
    store.save_pnl(sub_alpha.id, dates, sub_pnl)

    corr, target_id, method = compute_max_self_correlation_with_submitted(
        db_session, cand.id, pnl_store=store, min_overlap=500
    )
    assert method == "empirical"
    assert target_id == sub_alpha.id
    assert corr is not None and corr > 0.70


def test_evolution_diversity_gate(db_session):
    """Task 2d: Evolution generates child candidates from diverse parents."""
    from scripts.run_evolution import check_seed_diversity

    p1 = create_alpha(db_session, "rank(ts_zscore(close, 20))", AlphaSettings()).alpha
    p2 = create_alpha(db_session, "rank(ts_rank(volume, 20))", AlphaSettings()).alpha
    p3 = create_alpha(db_session, "rank(ts_delta(returns, 20))", AlphaSettings()).alpha

    is_div, found = check_seed_diversity([p1, p2, p3])
    assert is_div is True
    assert len(found) >= 3

    # If only one template
    is_div_mono, found_mono = check_seed_diversity([p1])
    assert is_div_mono is False
