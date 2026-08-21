"""Phase 5 tests — Thompson Sampler & Dataset Priority.

Tests:
1. Discounted Thompson Sampling reward updates.
2. Temporal discount factor (gamma = 0.95) decaying older rewards.
3. 20% dataset share diversity cap enforcement.
4. Simulation budget partition life-cycle.
5. Multi-arm posterior beta sampling.
"""

from __future__ import annotations

import random

from app.services.allocator import (
    DiscountedThompsonSampler,
    SimulationBudgetOrchestrator,
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


def test_simulation_budget_orchestrator_lifecycle() -> None:
    orchestrator = SimulationBudgetOrchestrator(daily_budget=15)
    alloc_boot = orchestrator.allocate_slots(has_confirmed_alphas=False)
    assert alloc_boot.mode == "bootstrap"
    assert alloc_boot.explore_slots == 2
    assert alloc_boot.confirm_slots == 1
    assert alloc_boot.evolution_slots == 0
    assert alloc_boot.explore_slots + alloc_boot.confirm_slots + alloc_boot.evolution_slots == 3

    alloc_mature = orchestrator.allocate_slots(has_confirmed_alphas=True)
    assert alloc_mature.mode == "mature"
    assert alloc_mature.explore_slots == 1
    assert alloc_mature.confirm_slots == 1
    assert alloc_mature.evolution_slots == 1
    assert (
        alloc_mature.explore_slots + alloc_mature.confirm_slots + alloc_mature.evolution_slots == 3
    )


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
