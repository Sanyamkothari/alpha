"""Typed AST for WorldQuant Fast-Expression formulas (functional form).

The parser emits these nodes; the validator walks them against the seeded
Operator KB + Field DB. See ``docs/ARCHITECTURE.md`` §5. Node ``start``/``end``
are character offsets into the source expression, used for error spans.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    # Source span (kw-only so subclasses keep clean positional signatures).
    start: int = field(default=0, kw_only=True)
    end: int = field(default=0, kw_only=True)


@dataclass
class Number(Node):
    """A numeric literal — window arg, scalar constant, or broadcastable matrix."""

    value: float
    is_int: bool = True


@dataclass
class Boolean(Node):
    """A ``true`` / ``false`` literal (for boolean-typed args)."""

    value: bool


@dataclass
class Field(Node):
    """A bare identifier resolved against ``data_fields`` (e.g. ``close``, ``sector``)."""

    name: str


@dataclass
class KeywordArg(Node):
    """A named argument, e.g. ``filter=true``."""

    name: str
    value: Node


@dataclass
class OperatorCall(Node):
    """An operator application, e.g. ``ts_mean(close, 20)``."""

    name: str
    args: list[Node] = field(default_factory=list)
    kwargs: list[KeywordArg] = field(default_factory=list)


def children(node: Node) -> list[Node]:
    """Direct child nodes of an ``OperatorCall`` (positional args + kwarg values)."""
    if isinstance(node, OperatorCall):
        return [*node.args, *(kw.value for kw in node.kwargs)]
    return []
