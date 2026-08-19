"""Shared CLI helpers."""
from __future__ import annotations

import functools
import sys

from app.services.brain.client import BrainAuthError, BrainError


def cli_main(fn):
    """Turn expected BRAIN failures into a clean message and exit code 1.

    Credential-less first runs are an expected outcome, not a crash; a traceback
    there reads as "this tool is broken" when the real message is one line long.
    Unexpected exceptions still propagate with their traceback intact.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BrainAuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(
                "hint: run `python -m app.seeds.seed_all` to work offline on the "
                "sample catalog instead.",
                file=sys.stderr,
            )
            return 1
        except BrainError as exc:
            print(f"error: BRAIN request failed — {exc}", file=sys.stderr)
            return 1
    return wrapper
