"""Phase 5 — Graded Discounted Thompson Sampling Allocator & Simulation Budget Orchestrator.

Implements:
1. Discounted Thompson Sampling (discount factor gamma = 0.95) over graded funnel rewards:
   - Base simulation: +0.1
   - Plateau clearance: +0.3
   - Sub-period stability: +0.6
   - Full DSR promotion: +1.0
2. Lifecycle-aware 3-slot budget orchestrator:
   - Bootstrap mode (< 5 passed): 80% Explore, 20% Plateau, 0% Evo
   - Mature mode (>= 5 passed): 40% Explore, 40% Plateau, 20% Evo
3. Priority draining of plateau confirmation queue (highest proxy score first).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Sequence

import structlog

log = structlog.get_logger("allocator_bandit")


@dataclass
class BanditArm:
    dataset_code: str
    alpha_param: float = 1.0  # Beta prior
    beta_param: float = 1.0
    total_trials: int = 0
    last_reward: float = 0.0


class DiscountedThompsonSampler:
    """Discounted Thompson Sampling bandit for non-stationary dataset reward distributions."""

    def __init__(self, discount_factor: float = 0.95) -> None:
        self.gamma = discount_factor
        self.arms: dict[str, BanditArm] = {}

    def get_arm(self, dataset_code: str) -> BanditArm:
        if dataset_code not in self.arms:
            self.arms[dataset_code] = BanditArm(dataset_code=dataset_code)
        return self.arms[dataset_code]

    def update(self, dataset_code: str, reward: float) -> None:
        """Update arm with graded reward in [0, 1] using discount factor gamma."""
        arm = self.get_arm(dataset_code)
        r = max(0.0, min(1.0, reward))

        # Apply temporal discount to existing evidence
        arm.alpha_param = max(1.0, 1.0 + (arm.alpha_param - 1.0) * self.gamma + r)
        arm.beta_param = max(1.0, 1.0 + (arm.beta_param - 1.0) * self.gamma + (1.0 - r))
        arm.total_trials += 1
        arm.last_reward = r

        log.debug(
            "bandit_arm_updated",
            dataset=dataset_code,
            reward=round(r, 2),
            alpha=round(arm.alpha_param, 2),
            beta=round(arm.beta_param, 2),
        )

    def sample_scores(self, dataset_codes: Sequence[str]) -> dict[str, float]:
        """Draw sample from posterior Beta distribution for each candidate dataset."""
        scores: dict[str, float] = {}
        for code in dataset_codes:
            arm = self.get_arm(code)
            # Sample from Beta(alpha, beta)
            sample = random.betavariate(arm.alpha_param, arm.beta_param)
            scores[code] = sample
        return scores

    def select_best_dataset(
        self,
        dataset_codes: Sequence[str],
        dataset_usage_counts: dict[str, int] | None = None,
        max_share: float = 0.20,
    ) -> str:
        """Select highest scoring dataset respecting the max_share diversity cap."""
        if not dataset_codes:
            return ""

        scores = self.sample_scores(dataset_codes)
        usage = dataset_usage_counts or {}
        total_usage = sum(usage.values()) or 1

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        # Check for candidates that do not exceed the diversity share cap
        eligible = [
            (code, score)
            for code, score in ranked
            if (usage.get(code, 0) / total_usage) < max_share
        ]

        if eligible:
            return eligible[0][0]

        # Fallback to the highest ranked dataset if all exceed the share cap
        return ranked[0][0]


@dataclass
class BudgetAllocation:
    explore_slots: int
    confirm_slots: int
    evolution_slots: int
    mode: str  # "bootstrap" | "mature"


class SimulationBudgetOrchestrator:
    """Partitions the platform's 3 concurrent simulation slots based on program lifecycle."""

    @staticmethod
    def get_allocation(passed_alpha_count: int, max_concurrent: int = 3) -> BudgetAllocation:
        if passed_alpha_count < 5:
            # Bootstrap mode: 80% exploration, 20% plateau confirmation, 0% evolution
            return BudgetAllocation(
                explore_slots=2,
                confirm_slots=1,
                evolution_slots=0,
                mode="bootstrap",
            )
        else:
            # Mature mode: 40% exploration, 40% plateau confirmation, 20% evolution
            return BudgetAllocation(
                explore_slots=1,
                confirm_slots=1,
                evolution_slots=1,
                mode="mature",
            )
