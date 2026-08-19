"""Load the Operator Knowledge Base from ``operators/operators.yaml`` (idempotent)."""

from __future__ import annotations

import structlog
import yaml

from app.config import OPERATORS_DIR
from app.db import session_scope
from app.models.enums import ArgType, OperatorCategory, ReturnType
from app.models.operators import (
    Operator,
    OperatorArgument,
    OperatorCompatibility,
    OperatorExample,
)

log = structlog.get_logger("seeds.operators")

OPERATORS_FILE = OPERATORS_DIR / "operators.yaml"

_VALID_CATEGORIES = {e.value for e in OperatorCategory}
_VALID_ARG_TYPES = {e.value for e in ArgType}
_VALID_RETURNS = {e.value for e in ReturnType}


def _norm_category(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in _VALID_CATEGORIES:
        log.warning("unknown_operator_category", value=value, fallback="special")
        return OperatorCategory.SPECIAL.value
    return v


def _norm_arg_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in _VALID_ARG_TYPES:
        log.warning("unknown_arg_type", value=value, fallback="matrix")
        return ArgType.MATRIX.value
    return v


def _norm_returns(value: str) -> str:
    v = (value or "matrix").strip().lower()
    if v not in _VALID_RETURNS:
        log.warning("unknown_return_type", value=value, fallback="matrix")
        return ReturnType.MATRIX.value
    return v


def load(db: Session) -> dict[str, int]:
    raw = yaml.safe_load(OPERATORS_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"expected a list of operators in {OPERATORS_FILE}")

    inserted = updated = 0
    existing = {op.name: op for op in db.query(Operator).all()}
    for item in raw:
        name = item["name"].strip()
        op = existing.get(name)
        if op is None:
            op = Operator(name=name)
            db.add(op)
            inserted += 1
        else:
            updated += 1
            # Re-seed children cleanly. Under autoflush=False the unit of
            # work would otherwise emit the re-appended INSERTs before the
            # orphan DELETEs and collide on UniqueConstraint(operator_id,
            # position); flush the deletes first to keep re-seeding idempotent.
            op.arguments.clear()
            op.examples.clear()
            op.compatibility.clear()
            db.flush()

        op.category = _norm_category(item.get("category", ""))
        op.definition = item.get("definition", "").strip()
        op.returns = _norm_returns(item.get("returns", "matrix"))
        op.restrictions = item.get("restrictions")
        op.typical_usage = item.get("typical_usage")
        op.is_seeded = True

        for arg in item.get("arguments", []) or []:
            op.arguments.append(
                OperatorArgument(
                    name=str(arg.get("name", "")),
                    position=int(arg.get("position", 0)),
                    arg_type=_norm_arg_type(arg.get("type", "matrix")),
                    required=bool(arg.get("required", True)),
                    is_window=bool(arg.get("window", False)),
                    min_value=arg.get("min"),
                    max_value=arg.get("max"),
                )
            )
        for ex in item.get("examples", []) or []:
            op.examples.append(
                OperatorExample(
                    expr=str(ex.get("expr", "")),
                    is_good=bool(ex.get("good", True)),
                    note=ex.get("note"),
                )
            )
        for other in item.get("compatible_with", []) or []:
            op.compatibility.append(
                OperatorCompatibility(other_operator_name=str(other), relation="wraps_well")
            )
        for other in item.get("avoid_nesting", []) or []:
            op.compatibility.append(
                OperatorCompatibility(other_operator_name=str(other), relation="avoid_nesting")
            )

    db.flush()
    total = db.query(Operator).count()
    n_args = db.query(OperatorArgument).count()
    result = {"inserted": inserted, "updated": updated, "total": total, "arguments": n_args}
    log.info("operators_seeded", **result)
    return result


def run() -> dict[str, int]:
    with session_scope() as db:
        return load(db)


if __name__ == "__main__":
    print(run())
