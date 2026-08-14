"""Module 4 read API — validate an expression against the seeded KB.

POST is to OUR own API (not BRAIN); the read-only boundary is unaffected.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.validation import validate_expression

router = APIRouter(prefix="/validate", tags=["validator"])


class ValidateIn(BaseModel):
    expression: str
    region: str = "USA"
    delay: int = 1
    universe: str = "TOP3000"


class IssueOut(BaseModel):
    code: str
    message: str
    span: tuple[int, int] | None = None


class ValidateOut(BaseModel):
    valid: bool
    normalized_expression: str | None
    errors: list[IssueOut]
    warnings: list[IssueOut]
    features: dict | None
    complexity_score: float | None
    structural_hash: str | None


@router.post("", response_model=ValidateOut)
def validate_endpoint(body: ValidateIn, db: Session = Depends(get_db)) -> ValidateOut:
    result = validate_expression(
        db, body.expression, region=body.region, delay=body.delay, universe=body.universe
    )
    return ValidateOut(
        valid=result.valid,
        normalized_expression=result.normalized_expression,
        errors=[IssueOut(**vars(e)) for e in result.errors],
        warnings=[IssueOut(**vars(w)) for w in result.warnings],
        features=result.features,
        complexity_score=result.complexity_score,
        structural_hash=result.structural_hash,
    )
