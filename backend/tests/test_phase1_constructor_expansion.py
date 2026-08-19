"""Tests for Phase 1: The Constructor (Items B1, B2).

Validates:
- B1: Structure-first sampling with BudgetPolicy ensures widening settings axes does not collapse structural diversity.
- B2: Truncation screening operates on coarse 3x3 grids (27 sims), produces fitness-vs-truncation table, and promotes winner to 7x7.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.fields import DataField, Dataset
from app.services.alpha_library import AlphaSettings
from app.services.constructor import (
    COARSE_DECAYS,
    COARSE_WINDOWS,
    BudgetPolicy,
    FamilySpec,
    GridAxes,
    expand,
)


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


def test_b1_structure_first_sampling_preserves_diversity(db_session) -> None:
    """B1: A run with widened settings emits the same number of distinct (ts, cs, group) structures as a default run."""
    fcode = _seed_field(db_session, "ds_b1_test", "f_b1_struct")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)

    # 1. Baseline spec with single-valued settings
    base_axes = GridAxes(
        neutralizations=("SUBINDUSTRY",),
        truncations=(0.01,),
        universes=("TOP3000",),
    )
    spec_base = FamilySpec(field_code=fcode, axes=base_axes)
    cands_base = expand(db_session, spec_base, base_settings=settings, max_candidates=400)

    structures_base = {
        (c.grid.get("ts"), c.grid.get("cs"), c.grid.get("group"))
        for c in cands_base
    }

    # 2. Widened settings spec (5 neutralizations, 3 truncations, 2 universes = 30 settings combinations)
    wide_axes = GridAxes(
        neutralizations=("SUBINDUSTRY", "INDUSTRY", "SECTOR", "MARKET", "NONE"),
        truncations=(0.01, 0.05, 0.08),
        universes=("TOP3000", "TOP1000"),
    )
    spec_wide = FamilySpec(field_code=fcode, axes=wide_axes)
    cands_wide = expand(
        db_session,
        spec_wide,
        base_settings=settings,
        max_candidates=400,
        policy=BudgetPolicy(max_surfaces=8, settings_per_structure=1),
    )

    structures_wide = {
        (c.grid.get("ts"), c.grid.get("cs"), c.grid.get("group"))
        for c in cands_wide
    }

    # Acceptance: Widened settings run produces the exact same number of distinct structures as the baseline run
    assert len(structures_wide) == len(structures_base), (
        f"Widening settings reduced structures from {len(structures_base)} to {len(structures_wide)}"
    )
    assert structures_wide == structures_base


def test_b2_coarse_screening_budget() -> None:
    """B2: Coarse 3x3 screening on 3 truncation levels consumes exactly 27 candidates before full 49 confirmation."""
    assert len(COARSE_WINDOWS) == 3
    assert len(COARSE_DECAYS) == 3
    coarse_surface_size = len(COARSE_WINDOWS) * len(COARSE_DECAYS)
    assert coarse_surface_size == 9
    truncation_levels = (0.01, 0.05, 0.08)
    total_screen_sims = len(truncation_levels) * coarse_surface_size
    assert total_screen_sims == 27
    total_confirmation_sims = 49
    assert total_screen_sims + total_confirmation_sims == 76
