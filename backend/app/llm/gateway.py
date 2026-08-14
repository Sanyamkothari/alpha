"""The LLM Gateway — the one entry point for all model calls.

Responsibilities:
* resolve a *capability* to a concrete model (via the registry),
* prepend the byte-stable cached prefix,
* serve from the output cache when possible,
* call the provider,
* record every call (incl. cache hits) to ``llm_runs`` with an estimated cost.

Nothing else in the codebase constructs a provider or names a model directly.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import structlog

from app.config import settings
from app.db import session_scope
from app.llm.base import LLMProvider, LLMRequest, LLMResult, Message
from app.llm.cache import OutputCache
from app.llm.prefix import build_cached_prefix
from app.llm.providers.fake_provider import FakeProvider
from app.llm.registry import Capability, estimate_cost_usd, resolve
from app.models.enums import LLMRunStatus
from app.models.prompts import LLMRun

log = structlog.get_logger("llm.gateway")


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider,
        output_cache: OutputCache | None = None,
        log_runs: bool = True,
    ) -> None:
        self.provider = provider
        self.cache = output_cache or OutputCache(enabled=settings.llm_output_cache)
        self.log_runs = log_runs

    # ---- internal: persist an llm_runs row ----
    def _record(
        self,
        *,
        resolved,
        result: LLMResult,
        task: str | None,
        module: str | None,
        prompt_key: str | None,
        prompt_version: int | None,
        latency_ms: int,
        status: LLMRunStatus,
        error: str | None = None,
    ) -> None:
        if not self.log_runs:
            return
        u = result.usage
        if status is LLMRunStatus.CACHED:
            # Output-cache hit: the provider was never called, so nothing was
            # billed. Record a zero-cost / zero-token row (the ``cached`` flag
            # marks the hit) so SUM(cost_usd)/SUM(tokens) over the ledger stay
            # truthful by default instead of double-counting the original call.
            input_tokens = output_tokens = cache_read = cache_creation = 0
            cost = 0.0
        else:
            input_tokens = u.input_tokens
            output_tokens = u.output_tokens
            cache_read = u.cache_read_tokens
            cache_creation = u.cache_creation_tokens
            # Prefer the provider's billed figure when it reports one. The tier
            # table is priced for Claude, so estimating a DeepSeek call from it
            # would be wrong by orders of magnitude — and "cost per submittable
            # alpha" is only useful if it is real money.
            cost = (
                result.reported_cost_usd
                if result.reported_cost_usd is not None
                else estimate_cost_usd(
                    resolved.tier,
                    input_tokens,
                    output_tokens,
                    cache_read,
                    cache_creation,
                )
            )
        try:
            with session_scope() as db:
                db.add(
                    LLMRun(
                        prompt_key=prompt_key,
                        prompt_version=prompt_version,
                        task=task,
                        module=module,
                        model_id=result.model_id,
                        tier=resolved.tier.value,
                        status=status.value,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read,
                        cache_creation_tokens=cache_creation,
                        cost_usd=cost,
                        latency_ms=latency_ms,
                        cached=result.cached,
                        error=error,
                    )
                )
        except Exception as exc:  # logging must never break the call path
            log.warning("llm_run_log_failed", error=str(exc))

    def _build_request(
        self,
        model_id: str,
        messages: list[Message],
        system: str | None,
        max_tokens: int,
        temperature: float,
        extra_prefix: str | None,
    ) -> LLMRequest:
        return LLMRequest(
            model_id=model_id,
            messages=messages,
            system=system,
            cached_prefix=build_cached_prefix(extra_prefix),
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def complete(
        self,
        capability: Capability,
        messages: list[Message] | str,
        *,
        system: str | None = None,
        task: str | None = None,
        module: str | None = None,
        prompt_key: str | None = None,
        prompt_version: int | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        extra_prefix: str | None = None,
        use_cache: bool = True,
    ) -> LLMResult:
        if isinstance(messages, str):
            messages = [Message(role="user", content=messages)]
        resolved = resolve(capability)
        req = self._build_request(
            resolved.model_id, messages, system, max_tokens, temperature, extra_prefix
        )

        if use_cache:
            hit = self.cache.get(req)
            if hit is not None:
                self._record(
                    resolved=resolved,
                    result=hit,
                    task=task,
                    module=module,
                    prompt_key=prompt_key,
                    prompt_version=prompt_version,
                    latency_ms=0,
                    status=LLMRunStatus.CACHED,
                )
                return hit

        start = time.perf_counter()
        try:
            result = self.provider.complete(req)
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            err_result = LLMResult(text="", model_id=resolved.model_id)
            self._record(
                resolved=resolved,
                result=err_result,
                task=task,
                module=module,
                prompt_key=prompt_key,
                prompt_version=prompt_version,
                latency_ms=latency,
                status=LLMRunStatus.ERROR,
                error=str(exc),
            )
            raise
        latency = int((time.perf_counter() - start) * 1000)

        if use_cache:
            self.cache.put(req, result)
        self._record(
            resolved=resolved,
            result=result,
            task=task,
            module=module,
            prompt_key=prompt_key,
            prompt_version=prompt_version,
            latency_ms=latency,
            status=LLMRunStatus.OK,
        )
        return result

    def stream(
        self,
        capability: Capability,
        messages: list[Message] | str,
        *,
        system: str | None = None,
        task: str | None = None,
        module: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        extra_prefix: str | None = None,
    ) -> Iterator[str]:
        if isinstance(messages, str):
            messages = [Message(role="user", content=messages)]
        resolved = resolve(capability)
        req = self._build_request(
            resolved.model_id, messages, system, max_tokens, temperature, extra_prefix
        )
        start = time.perf_counter()
        text_parts: list[str] = []
        # The provider's stream generator *returns* the final Usage (via
        # StopIteration.value), so usage is captured per-call rather than read
        # from shared mutable provider state — safe under concurrent streams.
        usage = None
        gen = self.provider.stream(req)
        try:
            while True:
                try:
                    delta = next(gen)
                except StopIteration as stop:
                    usage = stop.value
                    break
                text_parts.append(delta)
                yield delta
        except Exception as exc:
            # Mirror complete(): a mid-stream failure still gets an ERROR row.
            latency = int((time.perf_counter() - start) * 1000)
            err_result = LLMResult(text="".join(text_parts), model_id=resolved.model_id)
            self._record(
                resolved=resolved,
                result=err_result,
                task=task,
                module=module,
                prompt_key=None,
                prompt_version=None,
                latency_ms=latency,
                status=LLMRunStatus.ERROR,
                error=str(exc),
            )
            raise
        latency = int((time.perf_counter() - start) * 1000)
        result = LLMResult(text="".join(text_parts), model_id=resolved.model_id)
        if usage is not None:
            result.usage = usage
        self._record(
            resolved=resolved,
            result=result,
            task=task,
            module=module,
            prompt_key=None,
            prompt_version=None,
            latency_ms=latency,
            status=LLMRunStatus.OK,
        )


def build_provider() -> LLMProvider:
    """Pick a provider from settings (``LLM_PROVIDER`` = anthropic | openrouter | fake)."""
    if settings.llm_provider == "anthropic":
        from app.llm.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key, prompt_cache=settings.llm_prompt_cache
        )
    if settings.llm_provider == "openrouter":
        from app.llm.providers.openrouter_provider import OpenRouterProvider

        return OpenRouterProvider(api_key=settings.openrouter_api_key)
    return FakeProvider()


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    """Process-wide gateway singleton (FastAPI dependency-friendly)."""
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway(build_provider())
    return _gateway
