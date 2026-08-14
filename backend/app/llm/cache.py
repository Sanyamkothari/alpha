"""Output cache for LLM calls.

Keyed on the full request shape (model, system, cached prefix, messages, sampling
params). A hit returns a stored :class:`LLMResult` with ``cached=True`` and costs
nothing. Process-local dict today; a Phase 6 swap can back it with SQLite/Redis
behind the same interface.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from app.llm.base import LLMRequest, LLMResult


def request_key(req: LLMRequest) -> str:
    payload = {
        "model": req.model_id,
        "system": req.system,
        "prefix": req.cached_prefix,
        "messages": [(m.role, m.content) for m in req.messages],
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class OutputCache:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._store: dict[str, LLMResult] = {}

    def get(self, req: LLMRequest) -> LLMResult | None:
        if not self.enabled:
            return None
        hit = self._store.get(request_key(req))
        if hit is None:
            return None
        return replace(hit, cached=True)

    def put(self, req: LLMRequest, result: LLMResult) -> None:
        if not self.enabled:
            return
        self._store.setdefault(request_key(req), replace(result, cached=False))

    def clear(self) -> None:
        self._store.clear()
