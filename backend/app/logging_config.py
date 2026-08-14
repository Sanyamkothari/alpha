"""Structured logging (structlog) with secret redaction.

Call :func:`configure_logging` once at process start (the app factory does this).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

# Keys whose values must never be logged in full.
_SENSITIVE_KEYS = {
    "anthropic_api_key",
    "api_key",
    "authorization",
    "cookie",
    "password",
    "token",
    "jwt",
    "set-cookie",
}


def _redact_secrets(_logger: Any, _name: str, event_dict: dict) -> dict:
    """structlog processor: redact known-sensitive keys."""
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS and event_dict[key]:
            event_dict[key] = "***redacted***"
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_secrets,
        structlog.processors.StackInfoRenderer(),
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
