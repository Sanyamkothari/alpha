"""Anthropic provider with prompt caching + streaming.

The ``cached_prefix`` is sent as a system block carrying ``cache_control`` so the
provider caches it across calls (verify via ``usage.cache_read_input_tokens``).
Per the Claude API guidance for Opus 4.8 / Fable 5: we do NOT send
``budget_tokens``; reasoning depth, if enabled later, is controlled via effort.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from app.llm.base import LLMRequest, LLMResult, Usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, prompt_cache: bool = True) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the anthropic provider")
        import anthropic  # imported lazily so `fake` mode needs no SDK call path

        self._client = anthropic.Anthropic(api_key=api_key)
        self.prompt_cache = prompt_cache
        self.last_usage: Usage | None = None

    def _system_blocks(self, req: LLMRequest) -> list[dict[str, Any]] | None:
        blocks: list[dict[str, Any]] = []
        if req.cached_prefix:
            block: dict[str, Any] = {"type": "text", "text": req.cached_prefix}
            if self.prompt_cache:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        if req.system:
            blocks.append({"type": "text", "text": req.system})
        return blocks or None

    @staticmethod
    def _messages(req: LLMRequest) -> list[dict[str, Any]]:
        return [{"role": m.role, "content": m.content} for m in req.messages]

    @staticmethod
    def _usage_from(raw: Any) -> Usage:
        return Usage(
            input_tokens=getattr(raw, "input_tokens", 0) or 0,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        )

    def complete(self, req: LLMRequest) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": req.model_id,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": self._messages(req),
        }
        sys_blocks = self._system_blocks(req)
        if sys_blocks:
            kwargs["system"] = sys_blocks
        resp = self._client.messages.create(**kwargs)
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        )
        usage = self._usage_from(resp.usage)
        self.last_usage = usage
        return LLMResult(
            text=text,
            model_id=req.model_id,
            usage=usage,
            stop_reason=getattr(resp, "stop_reason", None),
        )

    def stream(self, req: LLMRequest) -> Generator[str, None, Usage]:
        kwargs: dict[str, Any] = {
            "model": req.model_id,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": self._messages(req),
        }
        sys_blocks = self._system_blocks(req)
        if sys_blocks:
            kwargs["system"] = sys_blocks
        with self._client.messages.stream(**kwargs) as stream:
            for delta in stream.text_stream:  # noqa: UP028 (post-loop usage capture needed)
                yield delta
            final = stream.get_final_message()
        usage = self._usage_from(final.usage)
        self.last_usage = usage
        return usage  # captured per-call by the gateway via StopIteration.value
