"""Capability registry: capability -> (model_id, tier) + cost estimation.

This indirection is the whole point of capability-tier binding (PLAN.md §2/§7):
tasks ask for ``DEEP_REASONING`` / ``FAST_GENERATION`` / ``CHEAP_CLASSIFICATION``
and the concrete model is resolved here from settings. To move a capability to a
different model (or provider), change the map — never the call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import settings
from app.models.enums import ModelTier


class Capability(StrEnum):
    DEEP_REASONING = "deep_reasoning"  # Critic, hypothesis refine, hard synthesis
    FAST_GENERATION = "fast_generation"  # formula variants, mutations, ranking
    CHEAP_CLASSIFICATION = "cheap_classification"  # field classify, dedup judge, summaries
    RESERVE = "reserve"  # break-glass only


# Capability -> tier. Stable; the tier -> concrete model_id mapping is config-driven.
_CAPABILITY_TIER: dict[Capability, ModelTier] = {
    Capability.DEEP_REASONING: ModelTier.OPUS,
    Capability.FAST_GENERATION: ModelTier.SONNET,
    Capability.CHEAP_CLASSIFICATION: ModelTier.HAIKU,
    Capability.RESERVE: ModelTier.RESERVE,
}


def _tier_model(tier: ModelTier) -> str:
    return {
        ModelTier.OPUS: settings.llm_model_opus,
        ModelTier.SONNET: settings.llm_model_sonnet,
        ModelTier.HAIKU: settings.llm_model_haiku,
        ModelTier.RESERVE: settings.llm_model_reserve,
    }[tier]


@dataclass(frozen=True)
class Resolved:
    capability: Capability
    tier: ModelTier
    model_id: str


def resolve(capability: Capability) -> Resolved:
    tier = _CAPABILITY_TIER[capability]
    return Resolved(capability=capability, tier=tier, model_id=_tier_model(tier))


# ---- Cost estimation (USD per 1M tokens). ESTIMATES — override to billed rates. ----
# Keyed by tier so a model swap keeps a sane cost estimate until updated.
_PRICING: dict[ModelTier, dict[str, float]] = {
    ModelTier.OPUS: {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    ModelTier.SONNET: {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    ModelTier.HAIKU: {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    ModelTier.RESERVE: {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
}


def estimate_cost_usd(
    tier: ModelTier,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    p = _PRICING.get(tier, _PRICING[ModelTier.SONNET])
    # "input_tokens" from the API already excludes cached/creation buckets.
    return round(
        (
            input_tokens * p["input"]
            + output_tokens * p["output"]
            + cache_read_tokens * p["cache_read"]
            + cache_creation_tokens * p["cache_write"]
        )
        / 1_000_000,
        6,
    )
