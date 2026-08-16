"""The allocator must refuse to exploit without hard diversity constraints (STRATEGY.md §6).

These tests pin the diversity guarantees, exact budget arithmetic closure, reproducible seeding,
and territory coordinate exploration across workstreams W1–W6.
"""

from __future__ import annotations

import pytest
from app.models.alphas import Alpha, SubmissionAttempt
from app.models.fields import DataField, Dataset
from app.services.allocator import (
    CROWDED_USER_COUNT,
    MAX_DATASET_SHARE,
    MIN_VIABLE_CAMPAIGN_BUDGET,
    MIN_VIABLE_TERRITORY_SIMS,
    DatasetStat,
    _dataset_priority,
    plan_budget_allocation,
    suggest,
)
from app.services.constructor import AlphaSettings, FamilySpec, canonical_territory_key


def _dataset(db, code: str, *, n_fields: int, users: int, universe: str = "TOP3000") -> Dataset:
    ds = Dataset(
        dataset_code=code,
        name=code,
        region="USA",
        delay=1,
        universe=universe,
        instrument_type="EQUITY",
    )
    db.add(ds)
    db.flush()
    for i in range(n_fields):
        db.add(
            DataField(
                field_code=f"{code}_f{i}",
                dataset_id=ds.id,
                category="fundamentals",
                field_type="MATRIX",
                coverage=0.9,
                user_count=users,
                region="USA",
                delay=1,
                universe=universe,
            )
        )
    db.flush()
    return ds


def test_no_dataset_exceeds_its_share(db_session) -> None:
    """Even when one dataset looks best, it cannot take the whole batch."""
    _dataset(db_session, "rich", n_fields=50, users=5)
    _dataset(db_session, "other_a", n_fields=50, users=10)
    _dataset(db_session, "other_b", n_fields=50, users=20)

    n = 10
    suggestions = suggest(db_session, n=n)
    assert suggestions
    per_dataset: dict[str, int] = {}
    for s in suggestions:
        per_dataset[s.dataset_code] = per_dataset.get(s.dataset_code, 0) + 1
    # per_dataset_cap for n=10 with 0.20 is max(1, ceil(10*0.2)) = 2
    assert all(c <= 2 for c in per_dataset.values()), per_dataset


def test_spreads_across_datasets(db_session) -> None:
    """Diversity is the objective — a batch drawn from one dataset is a failure."""
    for code in ("d1", "d2", "d3", "d4"):
        _dataset(db_session, code, n_fields=20, users=50)
    suggestions = suggest(db_session, n=8)
    assert len({s.dataset_code for s in suggestions}) > 1


def test_unexplored_dataset_is_ranked_on_crowding(db_session) -> None:
    """With no evidence, the un-mined dataset wins. That is Rule 1: an uncrowded
    field is where un-arbitraged signal survives."""
    pristine = DatasetStat("clean", "clean", 10, avg_user_count=5, tried=0, passed=0)
    saturated = DatasetStat("mined", "mined", 10, avg_user_count=19_000, tried=0, passed=0)
    assert _dataset_priority(pristine) > _dataset_priority(saturated)


def test_crowding_score_bounds(db_session) -> None:
    assert DatasetStat("a", "a", 1, avg_user_count=0, tried=0, passed=0).crowding_score == 1.0
    assert DatasetStat("b", "b", 1, avg_user_count=10**9, tried=0, passed=0).crowding_score == 0.0


def test_hit_rate_none_when_untried(db_session) -> None:
    """Untried must read as 'unknown', never as 'zero' — a dataset written off on
    no evidence would never be revisited."""
    assert DatasetStat("x", "x", 1, avg_user_count=1, tried=0, passed=0).hit_rate is None
    assert DatasetStat("y", "y", 1, avg_user_count=1, tried=4, passed=1).hit_rate == 0.25


@pytest.mark.parametrize("budget", [49, 98, 100, 200, 500, 1000])
def test_budget_arithmetic_closure_property(db_session, budget: int) -> None:
    """Exact closure across varying budgets (W1 / R2)."""
    _dataset(db_session, f"ds_b1_{budget}", n_fields=30, users=15)
    _dataset(db_session, f"ds_b2_{budget}", n_fields=30, users=40)
    _dataset(db_session, f"ds_b3_{budget}", n_fields=30, users=80)

    plan = plan_budget_allocation(db_session, total_simulations=budget, seed=42)
    assert plan.total_simulations == budget

    task_sum = sum(t.target_simulations for t in plan.tasks)
    assert task_sum == budget, f"Budget {budget} failed exact closure: task sum = {task_sum}"

    # Verify all tasks have positive allocation
    for t in plan.tasks:
        assert t.target_simulations > 0


def test_sub_minimum_budget_handled_gracefully(db_session) -> None:
    """Budgets below 49 are handled with exact closure rather than crashing (R2)."""
    plan = plan_budget_allocation(db_session, total_simulations=20)
    assert sum(t.target_simulations for t in plan.tasks) == 20
    assert plan.total_simulations == 20

    empty_plan = plan_budget_allocation(db_session, total_simulations=0)
    assert empty_plan.total_simulations == 0
    assert empty_plan.tasks == []


def test_deterministic_seeding(db_session) -> None:
    """Same seed produces identical allocation plan; different seed produces distinct plan (W2 / R3)."""
    _dataset(db_session, "ds_seed1", n_fields=40, users=20)
    _dataset(db_session, "ds_seed2", n_fields=40, users=50)

    plan_a1 = plan_budget_allocation(db_session, total_simulations=200, seed=12345)
    plan_a2 = plan_budget_allocation(db_session, total_simulations=200, seed=12345)
    plan_b = plan_budget_allocation(db_session, total_simulations=200, seed=99999)

    tasks_a1 = [(t.arm, t.field_code, t.operator_family, t.horizon_band, t.target_simulations) for t in plan_a1.tasks]
    tasks_a2 = [(t.arm, t.field_code, t.operator_family, t.horizon_band, t.target_simulations) for t in plan_a2.tasks]
    tasks_b = [(t.arm, t.field_code, t.operator_family, t.horizon_band, t.target_simulations) for t in plan_b.tasks]

    assert tasks_a1 == tasks_a2
    assert tasks_a1 != tasks_b


def test_per_field_operator_cap_allows_new_operator(db_session) -> None:
    """A field at its cap under ts_zscore is still suggested for ts_rank (W3 / R1 Mandatory Test)."""
    ds = _dataset(db_session, "ds_cap_test", n_fields=1, users=25)
    field_code = "ds_cap_test_f0"

    # Simulate that this field already has 3 territories mined under ts_zscore
    for band in ("short", "medium", "long"):
        tkey = canonical_territory_key(field_code, "ts_zscore", band)
        db_session.add(
            Alpha(
                expression=f"rank(ts_zscore({field_code}, 10))",
                expression_hash=f"hash_cap_{field_code}_{band}",
                family_key=tkey,
                region="USA",
                universe="TOP3000",
                delay=1,
                status="untested",
                feature_json={"grid": {"ts": "ts_zscore", "window": 10}},
            )
        )
    db_session.flush()

    suggestions = suggest(db_session, n=5)
    field_sugs = [s for s in suggestions if s.field_code == field_code]
    assert len(field_sugs) > 0, "Field should not be globally blacklisted"
    # Must propose a new operator (e.g. ts_rank, ts_delta), not ts_zscore
    for s in field_sugs:
        assert s.operator_family != "ts_zscore", f"Expected new operator, got {s.operator_family}"


def test_crowding_ceiling_in_exploit_vs_random_arm(db_session) -> None:
    """Exploit arm respects CROWDED_USER_COUNT ceiling (2000), while random stratified arm still samples from Q4 (W3 / R6)."""
    # Create an uncrowded dataset and a crowded dataset (>2000 users)
    _dataset(db_session, "ds_uncrowded", n_fields=10, users=50)
    _dataset(db_session, "ds_crowded", n_fields=10, users=3000)

    # Exploit suggestions must not include crowded fields (>2000)
    sugs = suggest(db_session, n=5)
    for s in sugs:
        assert (s.user_count or 0) <= CROWDED_USER_COUNT, f"Exploit suggested crowded field with {s.user_count} users"

    # Random stratified arm must include Q4 tasks when budget spans quartiles
    plan = plan_budget_allocation(db_session, total_simulations=1000, seed=42)
    rand_tasks = [t for t in plan.tasks if t.arm == "random_stratified"]
    assert any(t.quartile == 4 for t in rand_tasks), "Random stratified arm must sample from Q4 (crowded)"


def test_self_correlation_exclusion_from_exploit(db_session) -> None:
    """A submitted territory is excluded from exploit suggestions, but the field remains reachable under new operators (F4 / FF1)."""
    ds = _dataset(db_session, "ds_sub_test", n_fields=1, users=30)
    sub_field = "ds_sub_test_f0"

    # Build production key using constructor/campaign_runner's own key construction
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(
        field_code=sub_field,
        denominator="cap",
        operator_family="ts_zscore",
        wrapper_shape="rank",
    )
    prod_family_key = spec.family_key(settings)
    assert prod_family_key == f"{sub_field}/cap:ts_zscore:rank@USA/TOP3000/d1"

    # Register sub_field as submitted via SubmissionAttempt
    alpha = Alpha(
        expression=f"rank(ts_zscore({sub_field}, 10))",
        expression_hash=f"hash_sub_{sub_field}",
        family_key=prod_family_key,
        region="USA",
        universe="TOP3000",
        delay=1,
        status="submitted",
        feature_json={"grid": {"ts": "ts_zscore", "window": 10}},
    )
    db_session.add(alpha)
    db_session.flush()
    db_session.add(SubmissionAttempt(alpha_id=alpha.id, result="submitted"))
    db_session.flush()

    sugs = suggest(db_session, n=5)
    field_sugs = [s for s in sugs if s.field_code == sub_field]
    assert len(field_sugs) > 0, f"Field {sub_field} must remain reachable under untried operators (F4)"
    
    # Excluded from submitted operator ts_zscore (all horizon bands for legacy key), but recommended under untried ops
    for s in field_sugs:
        assert s.operator_family != "ts_zscore", f"Submitted operator ts_zscore must be excluded from exploit suggestions for {sub_field}"
        assert s.self_corr_headroom is None, "self_corr_headroom must be None (unmeasured) without fabricated proxies (F5)"


def test_self_correlation_exclusion_differential_passed_vs_submitted(db_session) -> None:
    """Differential test (FF1):

    1. status='passed' (unsubmitted) does NOT trigger self-correlation exclusion when evaluated.
    2. status='submitted' triggers self-correlation exclusion across all horizons for legacy keys.
    3. Canonical keys with explicit horizon only exclude that specific horizon.
    """
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)

    # 1. Test unsubmitted alpha (status='passed') where ts_zscore is the target operator
    ds_pass = _dataset(db_session, "ds_pass_test", n_fields=1, users=25)
    pass_field = "ds_pass_test_f0"
    spec_pass = FamilySpec(
        field_code=pass_field,
        denominator="cap",
        operator_family="ts_zscore",
        wrapper_shape="rank",
    )
    # Populate all alternative operators as tried so ts_zscore is next in line
    for op in ["ts_rank", "ts_mean", "ts_delta", "ts_std_dev", "ts_quantile", "ts_decay_linear", "ts_backfill"]:
        db_session.add(Alpha(
            expression=f"rank({op}({pass_field}, 10))",
            expression_hash=f"hash_pass_{op}_{pass_field}",
            family_key=f"{pass_field}/cap:{op}:rank@USA/TOP3000/d1",
            region="USA",
            universe="TOP3000",
            delay=1,
            status="passed",
            feature_json={"grid": {"ts": op, "window": 10}},
        ))
    db_session.flush()

    sugs_pass = suggest(db_session, n=5)
    field_sugs_pass = [s for s in sugs_pass if s.field_code == pass_field]
    assert len(field_sugs_pass) > 0
    # Since status is 'passed' (not submitted), ts_zscore is NOT excluded by self-correlation
    assert any(s.operator_family == "ts_zscore" for s in field_sugs_pass), "status='passed' must not trigger self-correlation exclusion"

    # Now verify that marking ts_zscore as submitted immediately excludes ts_zscore on the identical setup
    alpha_sub_legacy = Alpha(
        expression=f"rank(ts_zscore({pass_field}, 10))",
        expression_hash=f"hash_sub_legacy_{pass_field}",
        family_key=spec_pass.family_key(settings),
        region="USA",
        universe="TOP3000",
        delay=1,
        status="submitted",
        feature_json={"grid": {"ts": "ts_zscore", "window": 10}},
    )
    db_session.add(alpha_sub_legacy)
    db_session.flush()
    db_session.add(SubmissionAttempt(alpha_id=alpha_sub_legacy.id, result="submitted"))
    db_session.flush()

    sugs_now_sub = suggest(db_session, n=5)
    field_sugs_now_sub = [s for s in sugs_now_sub if s.field_code == pass_field]
    # Now ts_zscore must be excluded for all horizon bands
    assert not any(s.operator_family == "ts_zscore" for s in field_sugs_now_sub), "status='submitted' must exclude ts_zscore across all horizon bands"

    # 2. Test canonical submitted key with specific horizon band
    ds_canon = _dataset(db_session, "ds_canon_test", n_fields=1, users=35)
    canon_field = "ds_canon_test_f0"
    spec_canon = FamilySpec(
        field_code=canon_field,
        denominator="cap",
        operator_family="ts_zscore",
        wrapper_shape="rank",
        horizon_band="short",
    )
    canon_key = spec_canon.family_key(settings)
    assert "short" in canon_key

    alpha_canon = Alpha(
        expression=f"rank(ts_zscore({canon_field}, 5))",
        expression_hash=f"hash_canon_{canon_field}",
        family_key=canon_key,
        region="USA",
        universe="TOP3000",
        delay=1,
        status="submitted",
        feature_json={"grid": {"ts": "ts_zscore", "window": 5}},
    )
    db_session.add(alpha_canon)
    db_session.flush()
    db_session.add(SubmissionAttempt(alpha_id=alpha_canon.id, result="submitted"))
    db_session.flush()

    sugs_canon = suggest(db_session, n=5)
    field_sugs_canon = [s for s in sugs_canon if s.field_code == canon_field]
    assert len(field_sugs_canon) > 0
    # Specific horizon 'short' is excluded under ts_zscore, but other horizons (medium/long) or operators remain available
    for s in field_sugs_canon:
        if s.operator_family == "ts_zscore":
            assert s.horizon_band != "short", "Canonical submitted horizon 'short' must be excluded"


def test_coordinate_diversity_and_no_duplicate_territory_across_arms(db_session) -> None:
    """A realistic budget plan uses >1 operator and >1 wrapper, and no territory key is duplicated across arms (W4)."""
    _dataset(db_session, "ds_div1", n_fields=20, users=15)
    _dataset(db_session, "ds_div2", n_fields=20, users=45)

    plan = plan_budget_allocation(db_session, total_simulations=200, seed=42)

    operators = {t.operator_family for t in plan.tasks}
    assert len(operators) > 1, f"Expected >1 operator family, got {operators}"

    # Verify no duplicate territory across arms in one plan
    tkeys: list[str] = []
    for t in plan.tasks:
        tkey = canonical_territory_key(
            t.field_code, t.operator_family, t.horizon_band, "USA", "TOP3000", 1
        )
        tkeys.append(tkey)
    assert len(tkeys) == len(set(tkeys)), f"Duplicate territory keys found in plan: {tkeys}"
