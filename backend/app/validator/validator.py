"""The deterministic AST validator — Phase 1 keystone hard gate.

``validate(expression, kb)`` parses an expression and walks it against the seeded
Operator KB + Field DB, returning a semantic-analysis ``ValidationResult`` (NOT a
bare bool): ``{valid, ast, errors, warnings, normalized_expression, features,
complexity_score}``. It is a compiler front-end — the same result feeds storage
gating, complexity scoring, ranking, the Critic, mutation, and surrogate search.
Only ``valid is True`` expressions are ever stored. See ``docs/ARCHITECTURE.md`` §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.validator.ast_nodes import (
    Boolean,
    Field,
    Node,
    Number,
    OperatorCall,
)
from app.validator.features import extract_features
from app.validator.kb import ArgSpec, OpSpec, ValidatorKB
from app.validator.lexer import LexError
from app.validator.parser import ParseError, parse

# FieldType enum values (kept as literals so the validator has no model import).
_GROUP = "GROUP"
_MATRIX = "MATRIX"
_VECTOR = "VECTOR"


@dataclass
class Issue:
    """A validation error or warning with a stable machine code + source span."""

    code: str
    message: str
    span: tuple[int, int] | None = None


@dataclass
class ValidationResult:
    valid: bool
    expression: str
    normalized_expression: str | None
    ast: Node | None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    features: dict | None = None
    complexity_score: float | None = None
    structural_hash: str | None = None  # field/window-agnostic tree-shape key

    @property
    def error_codes(self) -> list[str]:
        return [e.code for e in self.errors]


def validate(expression: str, kb: ValidatorKB) -> ValidationResult:
    try:
        ast = parse(expression)
    except (ParseError, LexError) as exc:
        span = (exc.pos, exc.pos)
        return ValidationResult(
            valid=False,
            expression=expression,
            normalized_expression=None,
            ast=None,
            errors=[Issue("parse_error", str(exc), span)],
        )

    errors: list[Issue] = []
    warnings: list[Issue] = []
    _walk(ast, kb, errors, warnings)

    valid = not errors
    normalized = normalize(ast) if valid else None
    features, score = extract_features(ast, kb) if valid else (None, None)
    structural = features.get("structural_hash") if features else None
    return ValidationResult(
        valid=valid,
        expression=expression,
        normalized_expression=normalized,
        ast=ast,
        errors=errors,
        warnings=warnings,
        features=features,
        complexity_score=score,
        structural_hash=structural,
    )


# ---------------------------------------------------------------- walk + checks


def _walk(node: Node, kb: ValidatorKB, errors: list[Issue], warnings: list[Issue]) -> None:
    if isinstance(node, OperatorCall):
        spec = kb.operator(node.name)
        if spec is None:
            errors.append(
                Issue("unknown_operator", f"operator '{node.name}' is not in the KB", _span(node))
            )
        else:
            _check_signature(node, spec, kb, errors)
            _semantic_lint(node, spec, warnings)
        for child in node.args:
            _walk(child, kb, errors, warnings)
        for kw in node.kwargs:
            _walk(kw.value, kb, errors, warnings)
    elif isinstance(node, Field):
        if kb.field_type(node.name) is None:
            errors.append(
                Issue(
                    "unknown_field",
                    f"field '{node.name}' is not in the catalog "
                    f"for {kb.region}/{kb.universe}/delay{kb.delay}",
                    _span(node),
                )
            )


def _check_signature(
    call: OperatorCall, spec: OpSpec, kb: ValidatorKB, errors: list[Issue]
) -> None:
    specs = spec.args
    by_name = {s.name: s for s in specs}

    # Too many positional args.
    if len(call.args) > len(specs):
        errors.append(
            Issue(
                "arity",
                f"{call.name} takes at most {len(specs)} positional argument(s), "
                f"got {len(call.args)}",
                _span(call),
            )
        )

    # Per-position type/window checks. BRAIN's convention: required inputs are positional,
    # OPTIONAL parameters are keyword-only (e.g. winsorize(x, std=4), not winsorize(x, 4)). A
    # positional value landing on an optional slot is rejected here so the validator mirrors
    # BRAIN's acceptance instead of passing an expression BRAIN will refuse.
    for idx, arg in enumerate(call.args):
        if idx >= len(specs):
            break  # surplus already reported as an arity error
        if not specs[idx].required:
            errors.append(
                Issue(
                    "positional_optional",
                    f"{call.name} parameter '{specs[idx].name}' is optional and must be passed by "
                    f"keyword ('{specs[idx].name}=...'), not positionally",
                    _span(call),
                )
            )
        _check_arg(call, specs[idx], arg, kb, errors)

    # Keyword args must name a real parameter, are type-checked, and must not
    # collide with a positional filling the same slot or with an earlier keyword.
    seen_kwargs: set[str] = set()
    for kw in call.kwargs:
        target = by_name.get(kw.name)
        if target is None:
            errors.append(
                Issue(
                    "unknown_kwarg", f"{call.name} has no argument named '{kw.name}'", _span(call)
                )
            )
            continue
        if kw.name in seen_kwargs:
            errors.append(
                Issue(
                    "duplicate_kwarg",
                    f"{call.name} got multiple values for argument '{kw.name}'",
                    _span(call),
                )
            )
            continue
        seen_kwargs.add(kw.name)
        if target.position < len(call.args):
            errors.append(
                Issue(
                    "duplicate_arg",
                    f"{call.name} got argument '{kw.name}' both positionally and by keyword",
                    _span(call),
                )
            )
        _check_arg(call, target, kw.value, kb, errors)

    # Required-argument coverage (by position OR by keyword name).
    filled_positions = set(range(min(len(call.args), len(specs))))
    filled_names = {kw.name for kw in call.kwargs}
    for s in specs:
        if s.required and s.position not in filled_positions and s.name not in filled_names:
            errors.append(
                Issue(
                    "missing_arg",
                    f"{call.name} is missing required argument '{s.name}' (position {s.position})",
                    _span(call),
                )
            )


def _check_arg(
    call: OperatorCall, argspec: ArgSpec, node: Node, kb: ValidatorKB, errors: list[Issue]
) -> None:
    # An unknown field is reported once by the walk; don't also pile on a type error.
    if isinstance(node, Field) and kb.field_type(node.name) is None:
        return

    if argspec.is_window:
        if not (isinstance(node, Number) and node.is_int):
            errors.append(
                Issue(
                    "arg_type",
                    f"{call.name} argument '{argspec.name}' must be an integer window, "
                    f"got {_describe(node, kb)}",
                    _span(node),
                )
            )
            return
        lo = argspec.min_value if argspec.min_value is not None else 1.0
        hi = argspec.max_value if argspec.max_value is not None else float("inf")
        if not (lo <= node.value <= hi):
            hi_text = "inf" if hi == float("inf") else str(int(hi))
            errors.append(
                Issue(
                    "window_out_of_band",
                    f"{call.name} window '{argspec.name}'={int(node.value)} "
                    f"is outside the allowed band [{int(lo)}, {hi_text}]",
                    _span(node),
                )
            )
        return

    if _accepts(argspec.arg_type, node, kb):
        return

    if argspec.arg_type == _GROUP.lower():
        errors.append(
            Issue(
                "non_group_arg",
                f"{call.name} argument '{argspec.name}' must be a GROUP field "
                f"(e.g. sector/industry/country), got {_describe(node, kb)}",
                _span(node),
            )
        )
    else:
        errors.append(
            Issue(
                "arg_type",
                f"{call.name} argument '{argspec.name}' expects {argspec.arg_type}, "
                f"got {_describe(node, kb)}",
                _span(node),
            )
        )


def _accepts(expected: str, node: Node, kb: ValidatorKB) -> bool:
    """Whether ``node`` may fill an ``expected``-typed slot (ArgType values are lowercase)."""
    if expected == "int":
        return isinstance(node, Number) and node.is_int
    if expected in ("float", "const"):
        return isinstance(node, Number)
    if expected == "boolean":
        return isinstance(node, Boolean) or _returns(node, kb) == "boolean"
    if expected == "group":
        return isinstance(node, Field) and kb.field_type(node.name) == _GROUP
    if expected == "matrix":
        if isinstance(node, Number):
            return True  # scalar broadcast (e.g. group_mean(returns, 1, sector))
        if isinstance(node, Field):
            return kb.field_type(node.name) == _MATRIX
        if isinstance(node, OperatorCall):
            return _returns(node, kb) in ("matrix", None)  # None = unknown op, already flagged
        return False
    if expected == "vector":
        if isinstance(node, Number):
            return True
        if isinstance(node, Field):
            return kb.field_type(node.name) == _VECTOR
        if isinstance(node, OperatorCall):
            return _returns(node, kb) in ("vector", None)
        return False
    return True  # unknown expected type => be permissive


def _semantic_lint(call: OperatorCall, spec: OpSpec, warnings: list[Issue]) -> None:
    # Scope: some operators unlock only at higher BRAIN levels — warn, don't fail.
    if spec.scope:
        warnings.append(
            Issue("scope", f"operator '{call.name}' may require scope '{spec.scope}'", _span(call))
        )
    # Redundant nesting: rank(rank(...)) / normalize-of-normalize add no signal.
    redundant = {"rank", "normalize", "zscore", "scale"}
    if call.name in redundant:
        for child in call.args:
            if isinstance(child, OperatorCall) and child.name == call.name:
                warnings.append(
                    Issue(
                        "redundant_nesting",
                        f"nesting {call.name}(...) directly inside {call.name}(...) is redundant",
                        _span(call),
                    )
                )


# --------------------------------------------------------------------- helpers


def _returns(node: Node, kb: ValidatorKB) -> str | None:
    if isinstance(node, OperatorCall):
        spec = kb.operator(node.name)
        return spec.returns if spec else None
    return None


def _describe(node: Node, kb: ValidatorKB) -> str:
    if isinstance(node, Number):
        return f"the {'integer' if node.is_int else 'number'} {int(node.value) if node.is_int else node.value}"
    if isinstance(node, Boolean):
        return f"the boolean {str(node.value).lower()}"
    if isinstance(node, Field):
        ftype = kb.field_type(node.name)
        return f"field '{node.name}'" + (f" ({ftype})" if ftype else " (unknown)")
    if isinstance(node, OperatorCall):
        ret = _returns(node, kb)
        return f"{node.name}(...)" + (f" -> {ret}" if ret else "")
    return "value"


def _span(node: Node) -> tuple[int, int]:
    return (node.start, node.end)


def _normalize_number(node: Number) -> str:
    # Canonicalize numerically-equal literals so 5 / 5.0 and 0.0 / -0.0 dedup
    # to one hash. Integer-valued floats collapse to the int form.
    value = node.value
    if value == int(value):
        return str(int(value))
    return repr(value)


def normalize(node: Node) -> str:
    """Canonical, whitespace-free re-serialization of the AST (for hashing/dedup).

    Positional args keep their order (it is semantic); keyword args are emitted
    sorted by name (order-independent) so reordered named args dedup as one alpha.
    """
    if isinstance(node, Number):
        return _normalize_number(node)
    if isinstance(node, Boolean):
        return "true" if node.value else "false"
    if isinstance(node, Field):
        return node.name
    if isinstance(node, OperatorCall):
        parts = [normalize(a) for a in node.args]
        parts += sorted(f"{kw.name}={normalize(kw.value)}" for kw in node.kwargs)
        return f"{node.name}({','.join(parts)})"
    raise TypeError(f"cannot normalize node {type(node).__name__}")
