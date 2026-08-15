"""Phase 1 tests — Multi-Factor & Composite Constructor."""

from __future__ import annotations

import pytest

from app.services.alpha_library import AlphaSettings
from app.services.composite_constructor import CompositeSpec, expand_composite
from app.services.constructor import GridAxes
from app.services.validation import validate_expression

SMALL_AXES = GridAxes(
    ts_transforms=("ts_zscore", "ts_rank"),
    windows=(5, 22),
    cross_section=("rank", None),
    groups=(None, "sector"),
    neutralizations=("SUBINDUSTRY", "NONE"),
    decays=(0, 4),
    truncations=(0.08,),
    universes=("TOP3000",),
)


def test_composite_blend_emits_valid_expressions(db_session) -> None:
    spec = CompositeSpec(
        primary_field="close",
        secondary_field="volume",
        mechanism="momentum_volume_blend",
        mode="blend",
        axes=SMALL_AXES,
    )
    cands = expand_composite(db_session, spec, base_settings=AlphaSettings(), max_candidates=50)
    assert cands, "composite constructor emitted no blend candidates"
    for c in cands:
        res = validate_expression(
            db_session, c.expression, region="USA", delay=1, universe="TOP3000"
        )
        assert res.valid, f"invalid blend expression emitted: {c.expression}"
        assert "add(" in c.expression or "+" in c.expression or "multiply(" in c.expression


def test_composite_spread_emits_valid_expressions(db_session) -> None:
    spec = CompositeSpec(
        primary_field="close",
        secondary_field="vwap",
        mechanism="price_spread",
        mode="spread",
        axes=SMALL_AXES,
    )
    cands = expand_composite(db_session, spec, base_settings=AlphaSettings(), max_candidates=50)
    assert cands, "composite constructor emitted no spread candidates"
    for c in cands:
        res = validate_expression(
            db_session, c.expression, region="USA", delay=1, universe="TOP3000"
        )
        assert res.valid, f"invalid spread expression emitted: {c.expression}"
        assert "subtract(" in c.expression or "-" in c.expression


def test_composite_orthogonal_emits_valid_expressions(db_session) -> None:
    spec = CompositeSpec(
        primary_field="close",
        secondary_field="returns",
        mechanism="residual_momentum",
        mode="orthogonal",
        group_field="sector",
        axes=SMALL_AXES,
    )
    cands = expand_composite(db_session, spec, base_settings=AlphaSettings(), max_candidates=50)
    assert cands, "composite constructor emitted no orthogonal candidates"
    for c in cands:
        res = validate_expression(
            db_session, c.expression, region="USA", delay=1, universe="TOP3000"
        )
        assert res.valid, f"invalid orthogonal expression emitted: {c.expression}"
        assert "group_neutralize(" in c.expression


def test_composite_conditional_emits_valid_expressions(db_session) -> None:
    spec = CompositeSpec(
        primary_field="close",
        secondary_field="volume",
        trigger_field="volume",
        mechanism="volume_triggered_momentum",
        mode="conditional",
        axes=SMALL_AXES,
    )
    cands = expand_composite(db_session, spec, base_settings=AlphaSettings(), max_candidates=50)
    assert cands, "composite constructor emitted no conditional candidates"
    for c in cands:
        res = validate_expression(
            db_session, c.expression, region="USA", delay=1, universe="TOP3000"
        )
        assert res.valid, f"invalid conditional expression emitted: {c.expression}"
        assert "trade_when(" in c.expression


def test_composite_surfaces_are_complete(db_session) -> None:
    spec = CompositeSpec(
        primary_field="close",
        secondary_field="volume",
        mechanism="complete_surfaces_test",
        mode="blend",
        axes=SMALL_AXES,
    )
    cands = expand_composite(db_session, spec, base_settings=AlphaSettings(), max_candidates=20)
    surface_size = len(SMALL_AXES.windows) * len(SMALL_AXES.decays)  # 2 x 2 = 4
    groups: dict[tuple, int] = {}
    for c in cands:
        key = (
            c.grid["ts"],
            c.grid.get("cs"),
            c.grid.get("group"),
            c.grid["neutralization"],
            c.grid["truncation"],
        )
        groups[key] = groups.get(key, 0) + 1
    assert groups, "no composite surfaces emitted"
    assert set(groups.values()) == {surface_size}, f"incomplete composite surface: {groups}"
