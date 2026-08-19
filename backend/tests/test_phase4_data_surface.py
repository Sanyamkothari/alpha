"""Tests for Phase 4: Data Surface Gaps (Items D1, D2).

Validates:
- D1: Vector field reduction pipeline reduces VECTOR fields through reducers (vec_avg, vec_sum, vec_count, vec_max, vec_min) before time-series/cross-section transforms.
- D2: Event-time data templates generate valid days_from_last_change expressions on infrequent fields.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.fields import DataField, Dataset
from app.services.alpha_library import AlphaSettings
from app.services.constructor import FamilySpec, GridAxes, expand
from app.validator import ValidatorKB, validate


def _seed_vector_and_event_fields(db: Session) -> tuple[str, str]:
    ds = db.query(Dataset).filter_by(dataset_code="ds_d_test").first()
    if not ds:
        ds = Dataset(dataset_code="ds_d_test", name="ds_d_test", region="USA", universe="TOP3000", delay=1)
        db.add(ds)
        db.flush()

    # Seed VECTOR field (e.g. analyst or news vectors)
    vf = db.query(DataField).filter_by(field_code="f_analyst_recs").first()
    if not vf:
        vf = DataField(dataset_id=ds.id, field_code="f_analyst_recs", field_type="VECTOR", user_count=1)
        db.add(vf)

    # Seed MATRIX field with event nature (e.g. quarterly earnings)
    ef = db.query(DataField).filter_by(field_code="f_quarterly_eps").first()
    if not ef:
        ef = DataField(dataset_id=ds.id, field_code="f_quarterly_eps", field_type="MATRIX", user_count=1)
        db.add(ef)

    db.flush()
    return "f_analyst_recs", "f_quarterly_eps"


def test_d1_vector_field_reduction_pipeline(db_session) -> None:
    """D1: Vector field expansion produces valid ASTs across all 5 vector reducers with no type errors."""
    v_code, _ = _seed_vector_and_event_fields(db_session)
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    kb = ValidatorKB.from_session(db_session)

    axes = GridAxes(
        ts_transforms=("ts_zscore",),
        cross_section=("rank",),
        groups=(None,),
        vector_reducers=("vec_avg", "vec_sum", "vec_count", "vec_max", "vec_min"),
        windows=(5, 10),
        decays=(0,),
    )
    spec = FamilySpec(field_code=v_code, axes=axes)
    cands = expand(db_session, spec, base_settings=settings, max_candidates=100)

    assert len(cands) > 0

    reducers_found = set()
    for c in cands:
        r = c.grid.get("vec_reducer")
        if r:
            reducers_found.add(r)
            assert f"{r}({v_code})" in c.expression

        # Validate that AST is 100% valid with no type/arity errors
        val_res = validate(c.expression, kb)
        assert val_res.valid, f"Vector expression {c.expression} failed validation: {val_res.errors}"

    assert reducers_found == {"vec_avg", "vec_sum", "vec_count", "vec_max", "vec_min"}


def test_d2_event_time_templates(db_session) -> None:
    """D2: Infrequent data templates generate valid days_from_last_change expressions."""
    _, e_code = _seed_vector_and_event_fields(db_session)
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    kb = ValidatorKB.from_session(db_session)

    axes = GridAxes(
        ts_transforms=("ts_zscore",),
        cross_section=("rank",),
        groups=(None,),
        windows=(5, 10),
        decays=(0,),
    )
    spec = FamilySpec(field_code=e_code, is_event_field=True, axes=axes)
    cands = expand(db_session, spec, base_settings=settings, max_candidates=100)

    event_cands = [c for c in cands if c.grid.get("event_time")]
    assert len(event_cands) > 0

    for c in event_cands:
        assert f"days_from_last_change({e_code})" in c.expression
        val_res = validate(c.expression, kb)
        assert val_res.valid, f"Event-time expression {c.expression} failed validation: {val_res.errors}"
