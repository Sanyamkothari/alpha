"""Deterministic fake provider — no network, no API key.

Used for dev (``LLM_PROVIDER=fake``) and all tests. Output is a deterministic
function of the request so tests are reproducible. It also *simulates* provider-side
prompt caching: the first time it sees a given ``cached_prefix`` it reports
``cache_creation_tokens``; subsequent calls with the same prefix report
``cache_read_tokens`` — so the Phase 0 "cache_read > 0 on the second call" check
passes without a real API.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator

from app.llm.base import LLMRequest, LLMResult, Usage


def _approx_tokens(text: str) -> int:
    # ~4 chars/token heuristic; good enough for deterministic accounting.
    return max(1, len(text) // 4)


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self._seen_prefixes: set[str] = set()
        self.last_usage: Usage | None = None

    def _usage(self, req: LLMRequest, output: str) -> Usage:
        prefix = req.cached_prefix or ""
        prefix_tokens = _approx_tokens(prefix)
        msg_tokens = _approx_tokens((req.system or "") + "".join(m.content for m in req.messages))
        cache_read = 0
        cache_creation = 0
        if prefix:
            digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
            if digest in self._seen_prefixes:
                cache_read = prefix_tokens
            else:
                self._seen_prefixes.add(digest)
                cache_creation = prefix_tokens
        return Usage(
            input_tokens=msg_tokens,
            output_tokens=_approx_tokens(output),
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

    def _render(self, req: LLMRequest) -> str:
        last = req.messages[-1].content if req.messages else ""
        seed = hashlib.sha256((req.model_id + last).encode("utf-8")).hexdigest()[:8]
        return f"[fake:{req.model_id}] response to: {last[:120]} (seed={seed})"

    def complete(self, req: LLMRequest) -> LLMResult:
        text = self._render(req)
        usage = self._usage(req, text)
        self.last_usage = usage
        return LLMResult(text=text, model_id=req.model_id, usage=usage, stop_reason="end_turn")

    def stream(self, req: LLMRequest) -> Generator[str, None, Usage]:
        text = self._render(req)
        usage = self._usage(req, text)
        self.last_usage = usage
        for word in text.split(" "):
            yield word + " "
        return usage  # captured per-call by the gateway via StopIteration.value
