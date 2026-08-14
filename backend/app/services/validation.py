"""Validation service — build a ValidatorKB from the DB and validate an expression.

A thin seam so routers and the Alpha Library never touch the validator internals.
The KB is built per call from the request's session (a fast 2-query snapshot);
this keeps correctness simple across the dev DB and isolated test DBs. A cache with
proper invalidation can be added later if profiling warrants it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.validator import ValidationResult, ValidatorKB, validate


def validate_expression(
    db: Session,
    expression: str,
    *,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
) -> ValidationResult:
    kb = ValidatorKB.from_session(db, region=region, delay=delay, universe=universe)
    return validate(expression, kb)
