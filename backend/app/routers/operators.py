"""Module 2 read API — Operator Knowledge Base backing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.operators import Operator

router = APIRouter(prefix="/operators", tags=["operators"])


class ArgOut(BaseModel):
    name: str
    position: int
    arg_type: str
    required: bool
    is_window: bool
    min_value: float | None
    max_value: float | None

    model_config = {"from_attributes": True}


class ExampleOut(BaseModel):
    expr: str
    is_good: bool
    note: str | None

    model_config = {"from_attributes": True}


class OperatorOut(BaseModel):
    id: int
    name: str
    category: str
    definition: str
    returns: str
    restrictions: str | None
    typical_usage: str | None
    arguments: list[ArgOut]
    examples: list[ExampleOut]

    model_config = {"from_attributes": True}


class OperatorSummary(BaseModel):
    id: int
    name: str
    category: str
    definition: str
    returns: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[OperatorSummary])
def list_operators(
    db: Session = Depends(get_db),
    category: str | None = None,
    q: str | None = Query(None),
) -> list[OperatorSummary]:
    query = db.query(Operator)
    if category:
        query = query.filter(Operator.category == category)
    if q:
        query = query.filter(Operator.name.ilike(f"%{q}%"))
    rows = query.order_by(Operator.category, Operator.name).all()
    return [OperatorSummary.model_validate(r) for r in rows]


@router.get("/{name}", response_model=OperatorOut)
def get_operator(name: str, db: Session = Depends(get_db)) -> OperatorOut:
    op = (
        db.query(Operator)
        .options(selectinload(Operator.arguments), selectinload(Operator.examples))
        .filter(Operator.name == name)
        .first()
    )
    if op is None:
        raise HTTPException(status_code=404, detail="operator not found")
    return OperatorOut.model_validate(op)
