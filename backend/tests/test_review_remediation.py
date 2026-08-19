"""Regression tests for the code-review findings F1-F14.

Each test pins a behaviour that was wrong and is now right, named by finding so a
future regression points straight at what it broke.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.fields import DataField, Dataset
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import (
    DEFAULT_TRUNCATIONS,
    BudgetPolicy,
    FamilySpec,
    GridAxes,
    SurfaceConfig,
    _hump_variant_rank,
    expand,
    select_surface_configs,
)
from app.services.orthogonalization import (
    STANDARD_RISK_PROXIES,
    generate_residuals_for_alpha,
)
from app.services.plateau import _structure_of, structure_component, structure_replacing
from app.validator import ValidatorKB


def _seed_field(db: Session, ds_code: str, f_code: str, field_type: str = "MATRIX") -> str:
    ds = db.query(Dataset).filter_by(dataset_code=ds_code).first()
    if not ds:
        ds = Dataset(dataset_code=ds_code, name=ds_code, region="USA", universe="TOP3000", delay=1)
        db.add(ds)
        db.flush()
    f = db.query(DataField).filter_by(field_code=f_code).first()
    if not f:
        f = DataField(dataset_id=ds.id, field_code=f_code, field_type=field_type, user_count=1)
        db.add(f)
        db.flush()
    return f_code


# ---------------------------------------------------------------------------
# F2 — widening a structure-variant axis must not cost structural coverage
# ---------------------------------------------------------------------------


def test_f2_hump_variants_do_not_starve_structural_coverage() -> None:
    """The exact reproduction from the review: 7 transforms x 3 hump levels, budget 8.

    Before: 4 distinct transforms and zero un-humped surfaces, because ``hump(...)``
    sorted before ``ts_*`` and the stratum key embedded the hump level.
    """
    transforms = (
        "ts_zscore", "ts_rank", "ts_delta", "ts_mean",
        "ts_decay_linear", "ts_std_dev", "ts_quantile",
    )
    humps: tuple[float | None, ...] = (None, 0.01, 0.05)

    configs = [
        SurfaceConfig(
            layer=1,
            ts_sig=f"hump({ts},{h})" if h else ts,
            grid_extra={"ts": ts, "hump": h},
            builder_fn=lambda w: None,
            base_sig=ts,
            variant_rank=_hump_variant_rank(h, humps),
        )
        for ts in transforms
        for h in humps
    ]

    selected = select_surface_configs(configs, 8)

    assert len({c.base_sig for c in selected}) == len(transforms)
    control_arms = [c for c in selected if c.grid_extra["hump"] is None]
    assert len(control_arms) == len(transforms), "the un-humped control arm must survive"


def test_f2_control_arm_precedes_its_own_variants() -> None:
    """Within one structure, the control is drawn before any variant of it."""
    humps: tuple[float | None, ...] = (None, 0.01)
    configs = [
        SurfaceConfig(
            layer=1, ts_sig=f"hump(ts_rank,{h})" if h else "ts_rank",
            grid_extra={"ts": "ts_rank", "hump": h}, builder_fn=lambda w: None,
            base_sig="ts_rank", variant_rank=_hump_variant_rank(h, humps),
        )
        for h in humps
    ]
    selected = select_surface_configs(configs, 2)
    assert selected[0].grid_extra["hump"] is None


# ---------------------------------------------------------------------------
# F3 — the default settings tuple must be the historical baseline
# ---------------------------------------------------------------------------


def test_f3_default_truncation_is_the_reference_level(db_session) -> None:
    """settings_per_structure=1 emits combinations[0]; index 0 must be the baseline."""
    assert DEFAULT_TRUNCATIONS[0] == 0.08, "reference truncation must sort first"

    fcode = _seed_field(db_session, "ds_f3", "f_f3_trunc")
    cands = expand(
        db_session,
        FamilySpec(field_code=fcode),
        base_settings=AlphaSettings(region="USA", universe="TOP3000", delay=1),
        max_candidates=200,
    )
    assert cands
    assert {c.settings.truncation for c in cands} == {0.08}


# ---------------------------------------------------------------------------
# F7 — an explicitly supplied BudgetPolicy must actually bind
# ---------------------------------------------------------------------------


def test_f7_budget_policy_max_surfaces_is_honoured(db_session) -> None:
    fcode = _seed_field(db_session, "ds_f7", "f_f7_budget")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    axes = GridAxes(windows=(5, 10), decays=(0, 4))  # 4-point surfaces
    spec = FamilySpec(field_code=fcode, axes=axes)

    unbounded = expand(db_session, spec, base_settings=settings, max_candidates=400)
    bounded = expand(
        db_session, spec, base_settings=settings, max_candidates=400,
        policy=BudgetPolicy(max_surfaces=2, settings_per_structure=1),
    )

    assert len({_structure_of(c.grid) for c in bounded}) <= 2
    assert len(bounded) < len(unbounded)


# ---------------------------------------------------------------------------
# F11 — a submitted reference slice must not zero out the whole family
# ---------------------------------------------------------------------------


def test_f11_submitted_reference_slice_falls_through(db_session) -> None:
    """One used settings tuple should cost that tuple, not every structure."""
    fcode = _seed_field(db_session, "ds_f11", "f_f11_slice")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(field_code=fcode)
    family_key = spec.family_key(settings)

    baseline = expand(db_session, spec, base_settings=settings, max_candidates=200)
    assert baseline

    # Mark the reference slice (SUBINDUSTRY, 0.08) as already submitted.
    submitted = create_alpha(
        db_session, baseline[0].expression, baseline[0].settings,
        family_key=family_key, grid=baseline[0].grid, source="test",
    ).alpha
    submitted.status = AlphaStatus.SUBMITTED.value
    submitted.neutralization = "SUBINDUSTRY"
    submitted.truncation = 0.08
    db_session.flush()

    after = expand(db_session, spec, base_settings=settings, max_candidates=200)
    assert after, "family must fall through to the next settings tuple, not emit nothing"
    assert {c.settings.truncation for c in after} != {0.08}


# ---------------------------------------------------------------------------
# F10 / C2 — residual generation persists children with the right proxy
# ---------------------------------------------------------------------------


def test_f10_size_proxy_is_log_cap() -> None:
    """Raw cap is dominated by mega-caps; the KB's own example uses log(cap)."""
    assert STANDARD_RISK_PROXIES["size"] == "log(cap)"


def test_c2_residuals_are_persisted_as_children(db_session) -> None:
    fcode = _seed_field(db_session, "ds_c2w", "f_c2_wire")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    parent = create_alpha(
        db_session, f"rank(ts_zscore({fcode},20))", settings,
        family_key="fam_c2_wire", grid={"ts": "ts_zscore", "window": 20, "decay": 0}, source="test",
    ).alpha
    db_session.flush()

    created = generate_residuals_for_alpha(db_session, parent.id)
    assert created, "expected Tier-1 residuals"
    for child in created:
        assert child.parent_id == parent.id
        assert child.expression.startswith("regression_neut(")
        assert child.source == "orthogonalization"
        assert (child.feature_json or {}).get("grid", {}).get("residual_of")
        # A residual must not be structurally identical to its parent, or the
        # structural fallback in the correlation gate would reject it on sight.
        assert (child.feature_json or {}).get("structural_hash") != (
            parent.feature_json or {}
        ).get("structural_hash")


# ---------------------------------------------------------------------------
# A3 / N6 — structure tuple accessors
# ---------------------------------------------------------------------------


def test_structure_accessors_are_positional_safe() -> None:
    grid = {
        "ts": "ts_zscore", "cs": "rank", "group": None, "neutralization": "INDUSTRY",
        "truncation": 0.08, "universe": "TOP3000", "hump": None,
    }
    struct = _structure_of(grid)
    assert structure_component(struct, "neutralization") == "INDUSTRY"
    assert structure_component(struct, "universe") == "TOP3000"

    swapped = structure_replacing(struct, "neutralization", "SECTOR")
    assert structure_component(swapped, "neutralization") == "SECTOR"
    # Every other axis must be untouched.
    assert structure_component(swapped, "truncation") == 0.08
    assert structure_component(swapped, "ts") == "ts_zscore"
