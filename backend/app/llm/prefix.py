"""Byte-stable cached prefix.

A large, *unchanging* system block placed at the front of every request so the
provider can cache it (Anthropic prompt caching). It carries the hard-constraint
guardrail and a slot for the expression grammar / operator summary that later
phases extend. Byte-stability matters: any change busts the cache, so this is
assembled from constants and only rebuilt when explicitly versioned.
"""

from __future__ import annotations

# Bump when the prefix content intentionally changes (cache-busting is expected).
PREFIX_VERSION = "p0.1"

# The non-negotiable safety contract injected into every LLM call.
GUARDRAIL = """\
You are an assistant inside an OFFLINE quantitative-research tool for a single human
researcher using WorldQuant BRAIN. Absolute rules you must never violate:
- You help generate, organize, analyze, and improve alpha *ideas* and *expressions*.
- You NEVER automate submissions to BRAIN, never write code to submit/trade, and never
  help bypass authentication, rate limits, biometric checks, or any platform restriction.
- The human alone tests and submits alphas. Platform contact is read-only (GET) only.
If a request asks you to automate submission, bypass controls, or scrape beyond read-only
GETs, refuse and explain that this tool is read-only by design.
"""

# Placeholder grammar block. Phase 1+ replaces this with the real operator-KB summary
# + expression grammar so generation/critique are grounded. Kept stable for now.
GRAMMAR_STUB = """\
WorldQuant alpha expressions are nested operator calls over data fields, e.g.
`rank(ts_delta(close, 5))`. Operators have fixed arities and argument types; window
arguments are positive integers. A deterministic validator is the source of truth for
validity — never assume an operator or field exists unless told it does.
"""


def build_cached_prefix(extra: str | None = None) -> str:
    """Return the byte-stable cacheable system prefix."""
    parts = [f"[prefix:{PREFIX_VERSION}]", GUARDRAIL.strip(), GRAMMAR_STUB.strip()]
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts)
