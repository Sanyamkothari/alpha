"""Smoke-test the LLM gateway with the fake provider (no API key needed)."""

from __future__ import annotations

import sys

from app.db import session_scope
from app.llm import Capability, LLMGateway
from app.llm.cache import OutputCache
from app.llm.providers.fake_provider import FakeProvider
from app.models.prompts import LLMRun


def main() -> int:
    gw = LLMGateway(FakeProvider(), output_cache=OutputCache(enabled=True))

    r1 = gw.complete(
        Capability.DEEP_REASONING,
        "Generate a momentum hypothesis.",
        task="smoke",
        module="phase0",
        use_cache=False,
    )
    print("call1 model:", r1.model_id)
    print("call1 cache_creation > 0:", r1.usage.cache_creation_tokens > 0)

    # Same byte-stable prefix on the second call => provider reports a cache READ.
    r2 = gw.complete(
        Capability.FAST_GENERATION,
        "Generate 3 formula variants.",
        task="smoke",
        module="phase0",
        use_cache=False,
    )
    print("call2 model:", r2.model_id)
    print("call2 cache_read > 0:", r2.usage.cache_read_tokens > 0)

    # Output cache: identical request returns cached=True.
    a = gw.complete(Capability.CHEAP_CLASSIFICATION, "classify this field", task="smoke")
    b = gw.complete(Capability.CHEAP_CLASSIFICATION, "classify this field", task="smoke")
    print("output cache hit on repeat:", (not a.cached) and b.cached)

    with session_scope() as db:
        runs = db.query(LLMRun).filter(LLMRun.task == "smoke").count()
        cached_runs = (
            db.query(LLMRun).filter(LLMRun.task == "smoke", LLMRun.cached.is_(True)).count()
        )
    print("llm_runs logged:", runs, "| cached rows:", cached_runs)

    ok = (
        r1.model_id.endswith("opus-4-8")
        and r2.model_id.endswith("sonnet-4-6")
        and r1.usage.cache_creation_tokens > 0
        and r2.usage.cache_read_tokens > 0
        and b.cached
        and runs >= 4
    )
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
