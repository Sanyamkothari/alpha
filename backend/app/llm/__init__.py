"""LLM gateway package.

Public surface: capability-bound ``complete``/``stream`` via :class:`LLMGateway`.
Call sites import :class:`Capability` and :func:`get_gateway` — never a model id.
"""

from app.llm.base import LLMRequest, LLMResult, Message, Usage
from app.llm.gateway import LLMGateway, build_provider, get_gateway
from app.llm.registry import Capability, resolve

__all__ = [
    "Capability",
    "LLMGateway",
    "LLMRequest",
    "LLMResult",
    "Message",
    "Usage",
    "build_provider",
    "get_gateway",
    "resolve",
]
