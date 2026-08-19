"""Deterministic AST feature extraction (no LLM).

A pure function over a validated AST producing a ``feature_json`` breakdown and a
scalar ``complexity_score`` (both stored on ``alphas``). These curb low-quality
formula spam and feed ranking / the Critic / the future surrogate search. See
``docs/ARCHITECTURE.md`` §5. Weights are tunable and intentionally simple.
"""

from __future__ import annotations

import hashlib

from app.validator.ast_nodes import Boolean, Field, Node, Number, OperatorCall, children
from app.validator.kb import ValidatorKB

# complexity_score blend — emphasize depth and op-count, lightly count breadth.
_W_DEPTH = 0.5
_W_OPS = 0.35
_W_FIELDS = 0.15

# Window bucketing for the structural skeleton (trade-frequency bands).
_WINDOW_BANDS = ((5, "fast"), (21, "short"), (63, "med"), (252, "long"))


def _window_bucket(value: int) -> str:
    for limit, name in _WINDOW_BANDS:
        if value <= limit:
            return name
    return "vlong"


def _depth(node: Node) -> int:
    kids = children(node)
    if not kids:
        return 1
    return 1 + max(_depth(c) for c in kids)


def _max_branching(node: Node) -> int:
    best = len(children(node)) if isinstance(node, OperatorCall) else 0
    for c in children(node):
        best = max(best, _max_branching(c))
    return best


def _operator_count(node: Node) -> int:
    total = 1 if isinstance(node, OperatorCall) else 0
    return total + sum(_operator_count(c) for c in children(node))


def _fields(node: Node, acc: list[str]) -> None:
    if isinstance(node, Field):
        acc.append(node.name)
    for c in children(node):
        _fields(c, acc)


def _windows(node: Node, kb: ValidatorKB, acc: list[int]) -> None:
    if isinstance(node, OperatorCall):
        spec = kb.operator(node.name)
        if spec is not None:
            for idx, arg in enumerate(node.args):
                if idx < len(spec.args) and spec.args[idx].is_window:
                    if isinstance(arg, Number) and arg.is_int:
                        acc.append(int(arg.value))
            # Window args passed by keyword count too (else dedup/turnover corrupt).
            by_name = {s.name: s for s in spec.args}
            for kw in node.kwargs:
                s = by_name.get(kw.name)
                if s is not None and s.is_window:
                    if isinstance(kw.value, Number) and kw.value.is_int:
                        acc.append(int(kw.value.value))
    for c in children(node):
        _windows(c, kb, acc)


def structural_skeleton(node: Node, kb: ValidatorKB) -> str:
    """A canonical tree-shape string: fields abstracted to their type, windows to a
    band, constants to ``<INT>``/``<NUM>``. Two formulas with the same shape but
    different fields share a skeleton (e.g. ``rank(ts_rank(<MATRIX>,<WIN:long>))``)."""
    if isinstance(node, OperatorCall):
        spec = kb.operator(node.name)
        parts: list[str] = []
        for idx, arg in enumerate(node.args):
            is_window = spec is not None and idx < len(spec.args) and spec.args[idx].is_window
            if is_window and isinstance(arg, Number) and arg.is_int:
                parts.append(f"<WIN:{_window_bucket(int(arg.value))}>")
            else:
                parts.append(structural_skeleton(arg, kb))
        # Keyword args: bucket keyword windows like positional ones, and emit in a
        # canonical (sorted-by-name) order so reordered named args share a shape.
        by_name = {s.name: s for s in spec.args} if spec is not None else {}
        kw_parts: list[str] = []
        for kw in node.kwargs:
            s = by_name.get(kw.name)
            if s is not None and s.is_window and isinstance(kw.value, Number) and kw.value.is_int:
                kw_parts.append(f"{kw.name}=<WIN:{_window_bucket(int(kw.value.value))}>")
            else:
                kw_parts.append(f"{kw.name}={structural_skeleton(kw.value, kb)}")
        parts += sorted(kw_parts)
        return f"{node.name}({','.join(parts)})"
    if isinstance(node, Field):
        return f"<{kb.field_type(node.name) or 'UNKNOWN'}>"
    if isinstance(node, Number):
        return "<INT>" if node.is_int else "<NUM>"
    if isinstance(node, Boolean):
        return "<BOOL>"
    return "<?>"


def structural_hash(node: Node, kb: ValidatorKB) -> str:
    """SHA-256 of the structural skeleton — a field/window-agnostic dedup key for M10/M13."""
    return hashlib.sha256(structural_skeleton(node, kb).encode("utf-8")).hexdigest()


def all_subtree_skeletons(node: Node, kb: ValidatorKB) -> list[str]:
    """Extract structural skeletons for every subtree in the AST."""
    skeletons = [structural_skeleton(node, kb)]
    for child in children(node):
        skeletons.extend(all_subtree_skeletons(child, kb))
    return skeletons


def all_subtree_hashes(node: Node, kb: ValidatorKB) -> list[str]:
    """Compute SHA-256 hashes for all subtree skeletons in the AST."""
    return [hashlib.sha256(s.encode("utf-8")).hexdigest() for s in all_subtree_skeletons(node, kb)]


def extract_features(ast: Node, kb: ValidatorKB) -> tuple[dict, float]:
    depth = _depth(ast)
    branching = _max_branching(ast)
    operator_count = _operator_count(ast)

    field_names: list[str] = []
    _fields(ast, field_names)
    distinct_fields = sorted(set(field_names))

    windows: list[int] = []
    _windows(ast, kb, windows)
    shortest = min(windows) if windows else None

    # Turnover proxy: a shorter shortest-window implies faster signal => higher
    # turnover. Maps (0, inf) -> (0, 1]. A proxy, NEVER a real backtest.
    if shortest is not None:
        expected_turnover = round(1.0 / (1.0 + shortest / 21.0), 4)
    else:
        expected_turnover = 0.0

    interpretability = round(1.0 / (1.0 + depth + 0.5 * operator_count), 4)

    skeleton = structural_skeleton(ast, kb)
    sub_skeletons = all_subtree_skeletons(ast, kb)
    sub_hashes = [hashlib.sha256(s.encode("utf-8")).hexdigest() for s in sub_skeletons]

    feature_json = {
        "depth": depth,
        "branching": branching,
        "operator_count": operator_count,
        "time_windows": sorted(windows),
        "n_windows": len(windows),
        "shortest_window": shortest,
        "field_count": len(field_names),
        "distinct_fields": distinct_fields,
        "distinct_field_count": len(distinct_fields),
        "expected_turnover_proxy": expected_turnover,
        "interpretability": interpretability,
        "structural_skeleton": skeleton,
        "structural_hash": hashlib.sha256(skeleton.encode("utf-8")).hexdigest(),
        "subtree_skeletons": sub_skeletons,
        "subtree_hashes": sub_hashes,
    }

    complexity_score = round(
        _W_DEPTH * depth + _W_OPS * operator_count + _W_FIELDS * len(distinct_fields), 4
    )
    return feature_json, complexity_score
