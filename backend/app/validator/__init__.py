"""Module 4 — the deterministic AST formula validator (Phase 1 keystone).

Public surface::

    from app.validator import validate, ValidatorKB, ValidationResult

    kb = ValidatorKB.from_session(db)          # snapshot of the seeded KB
    result = validate("ts_mean(close, 20)", kb)
    if result.valid:
        store(result.normalized_expression, result.complexity_score, result.features)
"""

from __future__ import annotations

from app.validator.kb import ArgSpec, OpSpec, ValidatorKB
from app.validator.validator import Issue, ValidationResult, normalize, validate

__all__ = [
    "validate",
    "normalize",
    "ValidationResult",
    "Issue",
    "ValidatorKB",
    "OpSpec",
    "ArgSpec",
]
