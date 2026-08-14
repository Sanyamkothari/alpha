"""LLM gateway core types + the provider Protocol.

Call sites never name a model. They name a *capability* (see :mod:`app.llm.registry`);
the gateway resolves capability -> model_id -> provider. Swapping Claude/GPT/local
is therefore a config change, not a code change.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class LLMRequest:
    model_id: str
    messages: list[Message]
    system: str | None = None
    # Byte-stable block eligible for provider-side prompt caching.
    cached_prefix: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.7


@dataclass
class LLMResult:
    text: str
    model_id: str
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None
    cached: bool = False  # output-cache hit (our cache, not the provider's)
    # Actual billed cost when the provider reports one (OpenRouter does). The
    # gateway prefers this over its tier-based estimate — those tiers are priced
    # for Claude, so estimating a DeepSeek call from them is off by orders of
    # magnitude, and "cost per submittable alpha" is a headline metric.
    reported_cost_usd: float | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal surface every provider implements."""

    name: str

    def complete(self, req: LLMRequest) -> LLMResult: ...

    def stream(self, req: LLMRequest) -> Generator[str, None, Usage]:
        """Yield text deltas, then ``return`` the final :class:`Usage`.

        The gateway captures the returned usage per-call (via
        ``StopIteration.value``), so usage is never read from shared provider
        state and concurrent streams cannot cross-contaminate accounting.
        """
        ...
