"""OpenRouter provider — OpenAI-compatible chat completions.

OpenRouter fronts many vendors behind one OpenAI-shaped API, so the whole thing
is the ``openai`` SDK pointed at a different ``base_url``. Model ids carry a
vendor prefix (``deepseek/deepseek-v4-flash-0731``) and are supplied by config,
never hardcoded here.

Two differences from the Anthropic provider worth knowing:

* **No explicit prompt-cache control.** Anthropic takes a ``cache_control`` block;
  OpenRouter caching is provider-dependent and implicit. ``cached_prefix`` is
  therefore prepended to the system message and the cache-token counters stay at
  zero rather than being invented. A cost ledger that reports fabricated cache
  hits is worse than one that reports none.
* **Usage may be absent.** Some routed providers omit the ``usage`` block
  entirely; missing counters read as 0 rather than raising, because a cost-ledger
  gap must never fail a field-triage run.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from app.llm.base import LLMRequest, LLMResult, Usage

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: str, *, base_url: str = OPENROUTER_BASE_URL) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for the openrouter provider")
        import openai  # lazy: `fake` mode must not need the SDK

        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.last_usage: Usage | None = None

    @staticmethod
    def _messages(req: LLMRequest) -> list[dict[str, Any]]:
        """Fold system + cached_prefix into a leading system message.

        Order matters for whatever implicit caching the routed provider does:
        the byte-stable prefix goes first so a shared prefix stays a shared
        prefix across calls.
        """
        messages: list[dict[str, Any]] = []
        system_parts = [p for p in (req.cached_prefix, req.system) if p]
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})
        messages.extend({"role": m.role, "content": m.content} for m in req.messages)
        return messages

    @staticmethod
    def _usage_from(raw: Any) -> Usage:
        if raw is None:
            return Usage()
        prompt_details = getattr(raw, "prompt_tokens_details", None)
        return Usage(
            input_tokens=getattr(raw, "prompt_tokens", 0) or 0,
            # completion_tokens already includes reasoning_tokens on reasoning
            # models (deepseek-v4-flash spends most of its budget there), so this
            # is the billed figure — do not add them again.
            output_tokens=getattr(raw, "completion_tokens", 0) or 0,
            cache_read_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
            cache_creation_tokens=getattr(prompt_details, "cache_write_tokens", 0) or 0,
        )

    @staticmethod
    def _cost_from(raw: Any) -> float | None:
        """OpenRouter reports the real billed cost; prefer it to any estimate."""
        if raw is None:
            return None
        cost = getattr(raw, "cost", None)
        return float(cost) if isinstance(cost, (int, float)) else None

    def complete(self, req: LLMRequest) -> LLMResult:
        resp = self._client.chat.completions.create(
            model=req.model_id,
            messages=self._messages(req),
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
        choice = resp.choices[0]
        raw_usage = getattr(resp, "usage", None)
        usage = self._usage_from(raw_usage)
        self.last_usage = usage
        return LLMResult(
            # Reasoning models put their chain of thought in `reasoning` and the
            # answer in `content`. We want only the answer — but note that on a
            # tight max_tokens the reasoning can consume the whole budget and
            # leave content empty, which reads as a silent failure. Callers must
            # give reasoning models generous headroom.
            text=choice.message.content or "",
            model_id=req.model_id,
            usage=usage,
            stop_reason=getattr(choice, "finish_reason", None),
            reported_cost_usd=self._cost_from(raw_usage),
        )

    def stream(self, req: LLMRequest) -> Generator[str, None, Usage]:
        stream = self._client.chat.completions.create(
            model=req.model_id,
            messages=self._messages(req),
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        usage = Usage()
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = self._usage_from(chunk.usage)
            for choice in chunk.choices or []:
                delta = getattr(choice.delta, "content", None)
                if delta:
                    yield delta
        self.last_usage = usage
        return usage
