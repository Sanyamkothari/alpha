"""Validator knowledge base — an in-memory snapshot of the seeded Operator KB +
Field DB the validator checks against.

The validator NEVER hardcodes operators or fields: it reads ``operators`` /
``operator_arguments`` / ``data_fields`` (the source of truth). A ``ValidatorKB``
is a fast, read-only snapshot scoped to one ``(region, delay, universe)`` for
field resolution; operators are global.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.fields import DataField
from app.models.operators import Operator


@dataclass(frozen=True)
class ArgSpec:
    name: str
    position: int
    arg_type: str
    required: bool
    is_window: bool
    min_value: float | None
    max_value: float | None


@dataclass(frozen=True)
class OpSpec:
    name: str
    returns: str
    category: str
    scope: str | None
    args: tuple[ArgSpec, ...]  # ordered by position


class ValidatorKB:
    """Operator signatures + field-type lookup, scoped to a market config."""

    def __init__(
        self,
        operators: dict[str, OpSpec],
        field_types: dict[str, str],
        *,
        region: str = "USA",
        delay: int = 1,
        universe: str = "TOP3000",
    ) -> None:
        self._operators = operators
        self._field_types = field_types
        self.region = region
        self.delay = delay
        self.universe = universe

    def operator(self, name: str) -> OpSpec | None:
        return self._operators.get(name)

    def field_type(self, code: str) -> str | None:
        """Return the field's ``field_type`` (MATRIX/VECTOR/GROUP) or ``None`` if absent."""
        return self._field_types.get(code)

    @property
    def operator_count(self) -> int:
        return len(self._operators)

    @property
    def field_count(self) -> int:
        return len(self._field_types)

    # ---- builders ----
    @classmethod
    def from_session(
        cls,
        db: Session,
        *,
        region: str = "USA",
        delay: int = 1,
        universe: str = "TOP3000",
    ) -> ValidatorKB:
        operators: dict[str, OpSpec] = {}
        op_query = select(Operator).options(selectinload(Operator.arguments))
        for op in db.execute(op_query).scalars():
            args = tuple(
                ArgSpec(
                    name=a.name,
                    position=a.position,
                    arg_type=a.arg_type,
                    required=a.required,
                    is_window=a.is_window,
                    min_value=a.min_value,
                    max_value=a.max_value,
                )
                for a in sorted(op.arguments, key=lambda a: a.position)
            )
            operators[op.name] = OpSpec(
                name=op.name,
                returns=op.returns,
                category=op.category,
                scope=op.scope,
                args=args,
            )

        field_types: dict[str, str] = {}
        rows = db.execute(
            select(DataField.field_code, DataField.field_type).where(
                DataField.region == region,
                DataField.delay == delay,
                DataField.universe == universe,
            )
        ).all()
        for code, ftype in rows:
            field_types[code] = ftype

        return cls(operators, field_types, region=region, delay=delay, universe=universe)

    @classmethod
    def from_specs(
        cls,
        operators: dict[str, OpSpec],
        field_types: dict[str, str],
        **ctx: object,
    ) -> ValidatorKB:
        """Build directly from specs (used by unit tests that avoid a DB)."""
        return cls(operators, field_types, **ctx)  # type: ignore[arg-type]
