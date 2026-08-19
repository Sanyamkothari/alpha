"""Discounted Thompson Sampling bandit for non-stationary dataset reward distributions.

Implements:
1. Discounted Beta-Bernoulli multi-armed bandit with half-life temporal discount (F8).
2. Seeded pseudo-random generation for deterministic research execution (D4).
3. Multi-objective scoring combining expected pass rate and novelty/crowding headroom.
4. Robust 20% dataset share diversity cap with least-used fallback (no escape hatch).
5. Dynamic budget orchestrator allocating slots based on max_concurrent capacity.

Precedence:
- `allocator_bandit.py` is the learning-based arm allocator selecting high-potential, diverse datasets.
- `allocator.py` selects specific fields, operators, and territories within the dataset subject to
  hard crowding and feasibility constraints.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

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

    def __init__(
        self,
        discount_factor: float | None = None,
        half_life_days: float = 30.0,
        rng: random.Random | None = None,
    ) -> None:
        if discount_factor is not None:
            self.gamma = discount_factor
        else:
            self.gamma = 0.5 ** (1.0 / max(1.0, half_life_days))
        self.rng = rng or random.Random(42)
        self.arms: dict[str, BanditArm] = {}

    def get_arm(self, dataset_code: str) -> BanditArm:
        if dataset_code not in self.arms:
            self.arms[dataset_code] = BanditArm(dataset_code=dataset_code)
        return self.arms[dataset_code]

    def update(self, dataset_code: str, reward: float) -> None:
        """Update arm with a single trial reward in [0, 1]."""
        arm = self.get_arm(dataset_code)
        r = max(0.0, min(1.0, reward))
        arm.alpha_param = max(1.0, 1.0 + (arm.alpha_param - 1.0) * self.gamma + r)
        arm.beta_param = max(1.0, 1.0 + (arm.beta_param - 1.0) * self.gamma + (1.0 - r))
        arm.total_trials += 1
        arm.last_reward = r

    def update_batch(self, dataset_code: str, rewards: Sequence[float]) -> None:
        """Update arm by discounting once per batch, then accumulating batch rewards."""
        if not rewards:
            return
        arm = self.get_arm(dataset_code)
        sum_r = sum(max(0.0, min(1.0, r)) for r in rewards)
        n = len(rewards)
        # Apply discount once for the batch
        arm.alpha_param = max(1.0, 1.0 + (arm.alpha_param - 1.0) * self.gamma + sum_r)
        arm.beta_param = max(1.0, 1.0 + (arm.beta_param - 1.0) * self.gamma + (n - sum_r))
        arm.total_trials += n
        arm.last_reward = rewards[-1]

    def sample_scores(
        self,
        dataset_codes: Sequence[str],
        novelty_weights: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Draw sample from posterior Beta distribution for each candidate dataset,
        blended with novelty weights (expected_pass * novelty_vs_portfolio).
        """
        scores: dict[str, float] = {}
        weights = novelty_weights or {}
        for code in dataset_codes:
            arm = self.get_arm(code)
            sample = self.rng.betavariate(arm.alpha_param, arm.beta_param)
            novelty = weights.get(code, 1.0)
            scores[code] = sample * novelty
        return scores

    def select_best_dataset(
        self,
        dataset_codes: Sequence[str],
        dataset_usage_counts: dict[str, int] | None = None,
        max_share: float = 0.20,
        novelty_weights: dict[str, float] | None = None,
    ) -> str:
        """Select highest scoring dataset respecting the max_share diversity cap.
        If all datasets exceed the cap, fall back to the least-used eligible dataset.
        """
        if not dataset_codes:
            return ""
        scores = self.sample_scores(dataset_codes, novelty_weights=novelty_weights)
        usage = dataset_usage_counts or {}
        total_usage = sum(usage.values()) or 1

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        eligible = [
            (code, score)
            for code, score in ranked
            if (usage.get(code, 0) / total_usage) < max_share
        ]
        if eligible:
            return eligible[0][0]

        # No escape hatch: fall back to the least-used dataset
        least_used = sorted(dataset_codes, key=lambda c: usage.get(c, 0))
        return least_used[0]


@dataclass
class BudgetAllocation:
    explore_slots: int
    confirm_slots: int
    evolution_slots: int
    mode: str  # "bootstrap" | "mature"


class SimulationBudgetOrchestrator:
    """Orchestrates daily simulation budget across pipeline phases."""

    def __init__(self, daily_budget: int = 15, explore_ratio: float = 0.60) -> None:
        self.daily_budget = daily_budget
        self.explore_ratio = explore_ratio

    @classmethod
    def get_allocation(
        cls, passed_alpha_count: int = 0, max_concurrent: int = 3
    ) -> BudgetAllocation:
        mode = "bootstrap" if passed_alpha_count < 5 else "mature"
        if mode == "bootstrap":
            # 80% explore, 20% confirm
            exp_share = 0.80
            conf_share = 0.20
            evo_share = 0.00
        else:
            # 40% explore, 40% confirm, 20% evolution
            exp_share = 0.40
            conf_share = 0.40
            evo_share = 0.20

        raw_exp = max_concurrent * exp_share
        raw_conf = max_concurrent * conf_share
        raw_evo = max_concurrent * evo_share

        exp_slots = int(raw_exp)
        conf_slots = int(raw_conf)
        evo_slots = int(raw_evo)

        rem = max_concurrent - (exp_slots + conf_slots + evo_slots)
        fractions = [
            ("exp", raw_exp - exp_slots),
            ("conf", raw_conf - conf_slots),
            ("evo", raw_evo - evo_slots),
        ]
        fractions.sort(key=lambda x: x[1], reverse=True)
        for name, _ in fractions[:rem]:
            if name == "exp":
                exp_slots += 1
            elif name == "conf":
                conf_slots += 1
            elif name == "evo":
                evo_slots += 1

        return BudgetAllocation(
            explore_slots=exp_slots,
            confirm_slots=conf_slots,
            evolution_slots=evo_slots,
            mode=mode,
        )

    def allocate_slots(
        self, has_confirmed_alphas: bool = False, max_concurrent: int = 3
    ) -> BudgetAllocation:
        passed_count = 5 if has_confirmed_alphas else 0
        return self.get_allocation(passed_count, max_concurrent=max_concurrent)
