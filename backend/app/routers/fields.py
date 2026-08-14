"""Module 1 read API — Field Explorer backing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.fields import DataField

router = APIRouter(prefix="/fields", tags=["fields"])


class FieldOut(BaseModel):
    id: int
    field_code: str
    description: str | None
    category: str
    field_type: str
    coverage: float | None
    delay: int
    region: str
    universe: str
    unit: str | None

    model_config = {"from_attributes": True}


class FieldPage(BaseModel):
    total: int
    items: list[FieldOut]


@router.get("", response_model=FieldPage)
def list_fields(
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="substring match on code/description"),
    category: str | None = None,
    field_type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> FieldPage:
    query = db.query(DataField)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(DataField.field_code.ilike(like), DataField.description.ilike(like))
        )
    if category:
        query = query.filter(DataField.category == category)
    if field_type:
        query = query.filter(DataField.field_type == field_type)
    total = query.count()
    items = query.order_by(DataField.field_code).offset(offset).limit(limit).all()
    return FieldPage(total=total, items=[FieldOut.model_validate(i) for i in items])


@router.get("/{field_id}", response_model=FieldOut)
def get_field(field_id: int, db: Session = Depends(get_db)) -> FieldOut:
    field = db.get(DataField, field_id)
    if field is None:
        raise HTTPException(status_code=404, detail="field not found")
    return FieldOut.model_validate(field)
