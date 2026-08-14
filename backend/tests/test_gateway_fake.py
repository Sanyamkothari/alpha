"""Gateway behavior with the deterministic fake provider (no DB writes)."""

from __future__ import annotations

from app.config import settings
from app.llm import Capability, LLMGateway
from app.llm.cache import OutputCache
from app.llm.providers.fake_provider import FakeProvider
from app.llm.registry import resolve
from app.models.enums import ModelTier


def _gw() -> LLMGateway:
    # log_runs=False keeps this test free of DB side effects.
    return LLMGateway(FakeProvider(), output_cache=OutputCache(enabled=True), log_runs=False)


def test_capability_resolves_to_expected_tier_and_model() -> None:
    """Capability -> tier is fixed in code; tier -> model_id comes from config.

    These used to assert literal Claude ids ("opus-4-8"), which meant a
    legitimate config change — pointing the tiers at an OpenRouter model — broke
    the suite. The real invariant is the indirection itself: a call site names a
    capability and gets back whatever that tier is currently configured to use.
    """
    assert resolve(Capability.DEEP_REASONING).tier is ModelTier.OPUS
    assert resolve(Capability.FAST_GENERATION).tier is ModelTier.SONNET
    assert resolve(Capability.CHEAP_CLASSIFICATION).tier is ModelTier.HAIKU

    assert resolve(Capability.DEEP_REASONING).model_id == settings.llm_model_opus
    assert resolve(Capability.FAST_GENERATION).model_id == settings.llm_model_sonnet
    assert resolve(Capability.CHEAP_CLASSIFICATION).model_id == settings.llm_model_haiku


def test_prefix_cache_creation_then_read() -> None:
    gw = _gw()
    r1 = gw.complete(Capability.DEEP_REASONING, "first", use_cache=False)
    r2 = gw.complete(Capability.FAST_GENERATION, "second", use_cache=False)
    assert r1.usage.cache_creation_tokens > 0  # prefix first seen
    assert r2.usage.cache_read_tokens > 0  # same byte-stable prefix => read


def test_output_cache_hit_on_repeat() -> None:
    gw = _gw()
    a = gw.complete(Capability.CHEAP_CLASSIFICATION, "classify x")
    b = gw.complete(Capability.CHEAP_CLASSIFICATION, "classify x")
    assert a.cached is False
    assert b.cached is True
    assert a.text == b.text


def test_string_prompt_is_wrapped_as_user_message() -> None:
    gw = _gw()
    r = gw.complete(Capability.FAST_GENERATION, "hello")
    assert r.text  # produced something deterministic
    # Config-driven, not a hardcoded vendor string — see the resolve test above.
    assert r.model_id == settings.llm_model_sonnet
