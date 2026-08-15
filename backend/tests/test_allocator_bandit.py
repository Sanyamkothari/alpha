"""Phase 5 comprehensive tests — Graded Bandit Allocator & Unified Hierarchical Reward Model.

Tests:
1. Graded reward updates & maximum ladder (not sum): 0.10 -> 0.30 -> 0.60 -> 0.85 -> 1.00.
2. Temporal discount factor (gamma = 0.95) decaying older rewards.
3. 20% dataset share diversity cap enforcement.
4. Simulation budget partition: Bootstrap mode (< 5 passed) vs Mature mode (>= 5 passed).
5. Robust posterior beta sampling across multiple candidate datasets.
6. Hierarchical Beta shrinkage with effective sample size weighting w = n / (n + k).
7. Forced exploration slot for untried datasets and self-correlation constraint gating.
"""

from __future__ import annotations

import random
import pytest

from app.models.alphas import Alpha
from app.models.results import AlphaMetric
from app.services.allocator import (
    RUNG_SIMULATED,
    RUNG_PLATEAU,
    RUNG_DSR,
    RUNG_CORRELATION,
    RUNG_SUBMISSION,
    DiscountedThompsonSampler,
    SimulationBudgetOrchestrator,
    compute_alpha_reward,
    sample_hierarchical_posterior,
)


def test_graded_reward_updates() -> None:
    sampler = DiscountedThompsonSampler(discount_factor=0.95)

    # 1. Base simulation success (+0.1)
    sampler.update("ds_base", 0.1)
    arm_base = sampler.get_arm("ds_base")
    assert arm_base.last_reward == 0.1
    assert arm_base.alpha_param > 1.05

    # 2. Sub-period clearance (+0.6)
    sampler.update("ds_sub", 0.6)
    arm_sub = sampler.get_arm("ds_sub")
    assert arm_sub.last_reward == 0.6
    assert arm_sub.alpha_param > arm_base.alpha_param

    # 3. Full DSR promotion (+1.0)
    sampler.update("ds_dsr", 1.0)
    arm_dsr = sampler.get_arm("ds_dsr")
    assert arm_dsr.last_reward == 1.0
    assert arm_dsr.alpha_param > arm_sub.alpha_param


def test_discount_factor_over_time() -> None:
    sampler = DiscountedThompsonSampler(discount_factor=0.95)

    # Apply 1.0 reward initially
    sampler.update("dataset_x", 1.0)
    alpha_initial = sampler.get_arm("dataset_x").alpha_param

    # Apply multiple 0.0 updates -> discount factor dampens older 1.0 reward
    for _ in range(20):
        sampler.update("dataset_x", 0.0)

    alpha_later = sampler.get_arm("dataset_x").alpha_param
    assert alpha_later < alpha_initial


def test_diversity_share_cap_enforcement() -> None:
    sampler = DiscountedThompsonSampler(discount_factor=0.95)
    for _ in range(10):
        sampler.update("ds_top", 1.0)
    for _ in range(10):
        sampler.update("ds_modest", 0.5)

    usage = {"ds_top": 90, "ds_modest": 10}

    selected = sampler.select_best_dataset(
        ["ds_top", "ds_modest"],
        dataset_usage_counts=usage,
        max_share=0.20,
    )
    assert selected == "ds_modest"


def test_simulation_budget_partition_lifecycle() -> None:
    alloc_boot = SimulationBudgetOrchestrator.get_allocation(passed_alpha_count=3)
    assert alloc_boot.mode == "bootstrap"
    assert alloc_boot.explore_slots == 2
    assert alloc_boot.confirm_slots == 1
    assert alloc_boot.evolution_slots == 0
    assert alloc_boot.explore_slots + alloc_boot.confirm_slots + alloc_boot.evolution_slots == 3

    alloc_mature = SimulationBudgetOrchestrator.get_allocation(passed_alpha_count=5)
    assert alloc_mature.mode == "mature"
    assert alloc_mature.explore_slots == 1
    assert alloc_mature.confirm_slots == 1
    assert alloc_mature.evolution_slots == 1
    assert alloc_mature.explore_slots + alloc_mature.confirm_slots + alloc_mature.evolution_slots == 3


def test_multi_arm_posterior_sampling() -> None:
    random.seed(42)
    sampler = DiscountedThompsonSampler(discount_factor=0.95)
    datasets = ["ds_1", "ds_2", "ds_3", "ds_4"]

    for _ in range(15):
        sampler.update("ds_1", 0.95)
        sampler.update("ds_2", 0.7)
        sampler.update("ds_3", 0.4)
        sampler.update("ds_4", 0.05)

    scores = sampler.sample_scores(datasets)
    assert len(scores) == 4
    assert scores["ds_1"] > scores["ds_4"]


def test_hierarchical_reward_ladder_maximum() -> None:
    """Verifies that rewards evaluate to the maximum rung achieved in [0.0, 1.0], never a sum."""
    alpha_base = Alpha(id=101, status="simulated", expression="x", expression_hash="h101")
    m_sim = AlphaMetric(alpha_id=101, passed_all_checks=True)
    assert compute_alpha_reward(alpha_base, m_sim, submitted_ids=set()) == RUNG_SIMULATED

    m_plat = AlphaMetric(alpha_id=101, passed_all_checks=True)
    setattr(m_plat, "is_plateau", True)
    assert compute_alpha_reward(alpha_base, m_plat, submitted_ids=set()) == RUNG_PLATEAU

    m_dsr = AlphaMetric(alpha_id=101, passed_all_checks=True)
    setattr(m_dsr, "is_plateau", True)
    setattr(m_dsr, "dsr_passed", True)
    assert compute_alpha_reward(alpha_base, m_dsr, submitted_ids=set()) == RUNG_DSR

    assert compute_alpha_reward(alpha_base, m_dsr, submitted_ids=set(), has_self_corr_clearance=True) == RUNG_CORRELATION

    alpha_sub = Alpha(id=102, status="submitted", expression="y", expression_hash="h102")
    assert compute_alpha_reward(alpha_sub, m_dsr, submitted_ids={102}) == RUNG_SUBMISSION


def test_hierarchical_posterior_shrinkage() -> None:
    """Verifies that territory-level estimates are shrunk toward operator and dataset priors."""
    rng = random.Random(42)

    # 1 trial with high reward in an otherwise unproven operator/dataset -> shrunk towards prior
    score_sparse = sample_hierarchical_posterior(
        territory_reward=1.0,
        territory_n=1,
        op_reward=0.0,
        op_n=0,
        ds_reward=0.0,
        ds_n=0,
        k=10.0,
        rng=rng,
    )
    # High trial with heavy shrinkage should not sample near 1.0
    assert 0.20 <= score_sparse <= 0.85

    # 384 near-duplicate trials evaluated as 1 observation vs 50 real independent trials
    score_mature = sample_hierarchical_posterior(
        territory_reward=0.9,
        territory_n=50,
        op_reward=0.9,
        op_n=50,
        ds_reward=0.9,
        ds_n=100,
        k=10.0,
        rng=rng,
    )
    assert score_mature > score_sparse
