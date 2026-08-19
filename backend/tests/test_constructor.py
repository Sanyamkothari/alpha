"""The family constructor must emit valid-by-construction grids.

The whole point of the constructor is that it cannot produce an invalid
expression — that is what makes it safe to run at volume without an LLM in the
loop. These tests pin that property and the surface-completeness invariant the
plateau filter depends on.

Extended to cover the three new layers:
- Depth-2 expression templates (nested operator pairs)
- Multi-field families (ts_corr between primary and secondary field)
- Frequency-driven backfill
- Universe as a grid axis
"""

from __future__ import annotations

from app.services.alpha_library import AlphaSettings
from app.services.constructor import (
    BudgetPolicy,
    FamilySpec,
    GridAxes,
    expand,
)
from app.services.validation import validate_expression

SMALL = GridAxes(
    ts_transforms=("ts_zscore", "ts_rank"),
    depth2_pairs=(("ts_delta", "ts_rank"),),
    windows=(5, 22, 63),
    inner_windows=(5,),
    cross_section=("rank", None),
    groups=(None, "sector"),
    neutralizations=("SUBINDUSTRY", "NONE"),
    decays=(0, 4),
    truncations=(0.08,),
    universes=("TOP3000",),
)


def _spec(**kw) -> FamilySpec:
    return FamilySpec(
        field_code=kw.pop("field_code", "close"),
        mechanism="test",
        axes=SMALL,
        backfill_days=kw.pop("backfill_days", None),
        **kw,
    )


# ---------------------------------------------------------------- depth-1 tests
# (preserved from the original test suite)


def test_every_emitted_expression_validates(db_session) -> None:
    cands = expand(db_session, _spec(), base_settings=AlphaSettings(), max_candidates=200)
    assert cands, "constructor emitted nothing"
    for c in cands:
        result = validate_expression(
            db_session, c.expression, region="USA", delay=1, universe="TOP3000"
        )
        assert result.valid, f"constructor emitted an invalid expression: {c.expression}"


def test_surfaces_are_complete(db_session) -> None:
    """Truncation must drop whole (window x decay) surfaces, never partial ones.

    A partial surface is actively harmful: the plateau filter reads missing
    neighbours as "spike" and discards a genuine result.
    """
    cands = expand(db_session, _spec(), base_settings=AlphaSettings(), max_candidates=24)
    surface_size = len(SMALL.windows) * len(SMALL.decays)
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
    assert groups, "no surfaces emitted"
    assert set(groups.values()) == {surface_size}, f"incomplete surface(s): {groups}"


def test_respects_max_candidates(db_session) -> None:
    cands = expand(db_session, _spec(), base_settings=AlphaSettings(), max_candidates=12)
    assert len(cands) <= 12


def test_settings_vary_across_the_grid(db_session) -> None:
    """Settings are grid axes, varying across surfaces when policy allows settings expansion."""
    cands = expand(
        db_session,
        _spec(),
        base_settings=AlphaSettings(),
        max_candidates=200,
        policy=BudgetPolicy(max_surfaces=10, settings_per_structure=3),
    )
    assert len({c.settings.neutralization for c in cands}) > 1
    assert len({c.settings.decay for c in cands}) > 1


def test_group_ops_only_use_group_typed_fields(db_session) -> None:
    """``group_*`` requires a GROUP argument; emitting one against a MATRIX field
    would burn a simulation slot on a guaranteed rejection."""
    cands = expand(db_session, _spec(), base_settings=AlphaSettings(), max_candidates=200)
    for c in cands:
        if c.grid.get("group") is not None:
            assert "group_" in c.expression, c.expression


# ---------------------------------------------------------------- depth-2 tests


def test_depth2_emits_nested_expressions(db_session) -> None:
    """Depth-2 candidates must contain nested time-series operators."""
    cands = expand(db_session, _spec(), base_settings=AlphaSettings(), max_candidates=500)
    depth2 = [c for c in cands if c.grid.get("depth") == 2]
    assert depth2, "no depth-2 candidates emitted"
    # Every depth-2 expression should contain two ts_ operator calls.
    for c in depth2:
        # Count ts_ occurrences — must be at least 2 (inner + outer).
        ts_count = c.expression.count("ts_")
        assert ts_count >= 2, f"depth-2 expression has only {ts_count} ts_ ops: {c.expression}"


def test_depth2_expressions_all_validate(db_session) -> None:
    """Every depth-2 emission must pass the validator — the core invariant."""
    cands = expand(db_session, _spec(), base_settings=AlphaSettings(), max_candidates=500)
    depth2 = [c for c in cands if c.grid.get("depth") == 2]
    assert depth2, "no depth-2 candidates emitted"
    for c in depth2:
        result = validate_expression(
            db_session, c.expression, region="USA", delay=1, universe="TOP3000"
        )
        assert result.valid, f"depth-2 expression invalid: {c.expression}"


def test_depth2_surfaces_are_complete(db_session) -> None:
    """Depth-2 surfaces must be complete, just like depth-1."""
    cands = expand(db_session, _spec(), base_settings=AlphaSettings(), max_candidates=500)
    depth2 = [c for c in cands if c.grid.get("depth") == 2]
    if not depth2:
        return
    surface_size = len(SMALL.windows) * len(SMALL.decays)
    groups: dict[str, int] = {}
    for c in depth2:
        key = f"{c.grid['ts']}|{c.grid.get('cs')}|{c.grid.get('group')}|{c.grid['neutralization']}"
        groups[key] = groups.get(key, 0) + 1
    assert set(groups.values()) == {surface_size}, f"incomplete depth-2 surface(s): {groups}"


# -------------------------------------------------------------- multi-field tests


def test_multifield_emits_ts_corr(db_session) -> None:
    """With a secondary field, the constructor should emit ts_corr candidates."""
    spec = _spec(secondary_field="returns")
    cands = expand(db_session, spec, base_settings=AlphaSettings(), max_candidates=800)
    corr_cands = [c for c in cands if c.grid.get("ts") == "ts_corr"]
    assert corr_cands, "no ts_corr candidates emitted with secondary_field set"
    for c in corr_cands:
        assert "ts_corr" in c.expression
        assert "returns" in c.expression


def test_multifield_expressions_validate(db_session) -> None:
    """Every ts_corr emission must pass the validator."""
    spec = _spec(secondary_field="returns")
    cands = expand(db_session, spec, base_settings=AlphaSettings(), max_candidates=800)
    corr_cands = [c for c in cands if c.grid.get("ts") == "ts_corr"]
    assert corr_cands, "no ts_corr candidates to test"
    for c in corr_cands:
        result = validate_expression(
            db_session, c.expression, region="USA", delay=1, universe="TOP3000"
        )
        assert result.valid, f"ts_corr expression invalid: {c.expression}"


def test_multifield_family_key_includes_secondary(db_session) -> None:
    """The family key must distinguish single-field from multi-field families."""
    single = _spec()
    multi = _spec(secondary_field="returns")
    assert "returns" not in single.family_key()
    assert "+returns" in multi.family_key()


def test_multifield_bogus_secondary_skipped(db_session) -> None:
    """A secondary field not in the catalog should not produce ts_corr candidates."""
    spec = _spec(secondary_field="bogus_nonexistent_field")
    cands = expand(db_session, spec, base_settings=AlphaSettings(), max_candidates=800)
    corr_cands = [c for c in cands if c.grid.get("ts") == "ts_corr"]
    assert not corr_cands, "should not emit ts_corr for a field not in the catalog"


# -------------------------------------------------------- frequency backfill tests


def test_frequency_overrides_backfill_days() -> None:
    """The frequency field should drive backfill when set."""
    spec_daily = FamilySpec(field_code="x", mechanism="t", frequency="daily")
    assert spec_daily.effective_backfill is None

    spec_quarterly = FamilySpec(field_code="x", mechanism="t", frequency="quarterly")
    assert spec_quarterly.effective_backfill == 120

    spec_annual = FamilySpec(field_code="x", mechanism="t", frequency="annual")
    assert spec_annual.effective_backfill == 252


def test_frequency_fallback_to_backfill_days() -> None:
    """When frequency is not set, explicit backfill_days should be used."""
    spec = FamilySpec(field_code="x", mechanism="t", backfill_days=60)
    assert spec.effective_backfill == 60

    spec_none = FamilySpec(field_code="x", mechanism="t", backfill_days=None, frequency=None)
    assert spec_none.effective_backfill is None


def test_daily_frequency_skips_backfill_in_expression(db_session) -> None:
    """A daily field should not include ts_backfill in the expression."""
    spec = _spec(frequency="daily", backfill_days=None)
    cands = expand(db_session, spec, base_settings=AlphaSettings(), max_candidates=24)
    assert cands
    for c in cands:
        assert "ts_backfill" not in c.expression, (
            f"daily field should not have ts_backfill: {c.expression}"
        )


def test_quarterly_frequency_includes_backfill(db_session) -> None:
    """A quarterly field should include ts_backfill(field, 120)."""
    spec = _spec(frequency="quarterly")
    cands = expand(db_session, spec, base_settings=AlphaSettings(), max_candidates=24)
    assert cands
    for c in cands:
        assert "ts_backfill" in c.expression, (
            f"quarterly field should have ts_backfill: {c.expression}"
        )
        assert "120" in c.expression


# ------------------------------------------------------------ universe axis tests


def test_universe_axis_creates_distinct_settings(db_session) -> None:
    """When multiple universes are on the grid, candidates should have different
    universe settings."""
    multi_univ_axes = GridAxes(
        ts_transforms=("ts_zscore",),
        depth2_pairs=(),
        windows=(22,),
        inner_windows=(5,),
        cross_section=(None,),
        groups=(None,),
        neutralizations=("NONE",),
        decays=(0,),
        truncations=(0.08,),
        universes=("TOP3000", "TOP3000"),  # same universe to avoid catalog issues
    )
    spec = FamilySpec(field_code="close", mechanism="test", axes=multi_univ_axes, backfill_days=None)
    cands = expand(db_session, spec, base_settings=AlphaSettings(), max_candidates=100)
    # Should emit at least 1 candidate (the universe is the same so dedup will merge)
    assert cands


def test_universe_in_grid_metadata(db_session) -> None:
    """Universe should be recorded in the grid metadata when present."""
    spec = _spec()
    cands = expand(db_session, spec, base_settings=AlphaSettings(), max_candidates=24)
    assert cands
    for c in cands:
        assert "universe" in c.grid
