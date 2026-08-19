"""Tests for Phase 2: Turnover Lever (Item B3: Hump operator).

Validates:
- B3: hump is a structural axis applied outside cross-sectional operations, updating expression, hash, and grid.
- B3: (decay x hump) provides a 2D parameter space for controlling turnover and Sharpe.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.fields import DataField, Dataset
from app.services.alpha_library import AlphaSettings
from app.services.constructor import FamilySpec, GridAxes, expand
from app.validator import ValidatorKB, validate


def _seed_field(db: Session, ds_code: str, f_code: str) -> str:
    ds = db.query(Dataset).filter_by(dataset_code=ds_code).first()
    if not ds:
        ds = Dataset(dataset_code=ds_code, name=ds_code, region="USA", universe="TOP3000", delay=1)
        db.add(ds)
        db.flush()
    f = db.query(DataField).filter_by(field_code=f_code).first()
    if not f:
        f = DataField(dataset_id=ds.id, field_code=f_code, field_type="MATRIX", user_count=1)
        db.add(f)
        db.flush()
    return f_code


def test_b3_hump_structural_axis(db_session) -> None:
    """B3: Hump operator is properly applied outside cross-sectional wrap and valid under AST validator."""
    fcode = _seed_field(db_session, "ds_b3_test", "f_b3_hump")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    kb = ValidatorKB.from_session(db_session)

    axes = GridAxes(
        ts_transforms=("ts_zscore",),
        cross_section=("rank",),
        groups=(None,),
        humps=(None, 0.02),
        windows=(5, 10),
        decays=(0, 4),
    )
    spec = FamilySpec(field_code=fcode, axes=axes)
    cands = expand(db_session, spec, base_settings=settings, max_candidates=100)

    hump_cands = [c for c in cands if c.grid.get("hump") == 0.02]
    no_hump_cands = [c for c in cands if c.grid.get("hump") is None]

    assert len(hump_cands) > 0
    assert len(no_hump_cands) > 0

    # Ensure hump expressions are properly wrapped: hump(rank(...), 0.02)
    for c in hump_cands:
        assert c.expression.startswith("hump(")
        assert "0.02" in c.expression
        res = validate(c.expression, kb)
        assert res.valid, f"Expression {c.expression} failed validation: {res.reasons}"

    # Ensure distinct hashes between hump and non-hump versions
    c_hump_map = {(c.grid["window"], c.grid["decay"]): c for c in hump_cands}
    c_nohump_map = {(c.grid["window"], c.grid["decay"]): c for c in no_hump_cands}
    for coord in c_hump_map:
        if coord in c_nohump_map:
            assert c_hump_map[coord].expression != c_nohump_map[coord].expression
            assert c_hump_map[coord].features["structural_hash"] != c_nohump_map[coord].features["structural_hash"]
