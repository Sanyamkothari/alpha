"""Stage 5 — Deciding What to Try Next (STRATEGY.md §6 & Protocol v2).

This is what turns the pipeline from a tool you drive into a machine that runs.
It answers one question without the operator: *which field and territory should
the next family be built on?*

The counterintuitive part, and the reason a naive textbook bandit is wrong here:
**it must refuse to exploit without hard constraints.** A greedy allocator finds
the best dataset and pours everything into it, which produces a pile of
mutually-correlated alphas — and BRAIN pays only for *uncorrelated* ones, so most
of that output is worth nothing. Portfolio diversity beats marginal Sharpe.

Constrain-Then-Rank & Coordinate Diversification
-----------------------------------------------
1. **Hard Feasibility Constraints**:
   - `MAX_DATASET_SHARE` (20%) caps any single dataset's slice of a batch.
   - Forced exploration slots for untried datasets and untried operator families.
   - `CROWDED_USER_COUNT` (2,000 users) ceiling and `NEGLECTED_USER_COUNT` (5 users)
     floor on the exploit arm.
   - `MAX_TERRITORIES_PER_FIELD_OP` (3) saturation cap per `(field_code, operator_family)`.
   - **Territory-Level Exclusion**: Specific territories `(field_code, operator_family, horizon_band)`
     that produced confirmed submissions are excluded from the exploit path. A field with a
     submitted alpha remains reachable under other untried operator families or horizon bands.

2. **Empirical Dataset Priority**:
   Datasets are ranked using measured simulation hit-rate (simulations passing all checks)
   blended with uncrowded user count priority:
   `score = 0.6 * min(1.0, hit_rate * 10) + 0.4 * crowding_score` (for explored datasets)
   `score = crowding_score` (for untried datasets).
   The allocator does not use an unverified multi-rung reward ladder or synthetic posteriors;
   unmeasured properties (e.g. self-correlation headroom) are reported as None.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, SubmissionAttempt
from app.models.fields import DataField, Dataset
from app.services.field_health import dead_field_codes
from app.models.results import AlphaMetric
from app.services.constructor import (
    DEFAULT_CROSS_SECTION,
    DEFAULT_TS_TRANSFORMS,
    TerritorySignature,
    canonical_territory_key,
    parse_territory_signature,
)
from app.services.plateau import family_field_code

log = structlog.get_logger("allocator")

# No dataset may take more than this share of a batch, however well it scores.
MAX_DATASET_SHARE = 0.20

# Fields this heavily used are effectively fully arbitraged in the exploit path.
CROWDED_USER_COUNT = 2_000

# Below this coverage a field is too sparse to build a stable signal on.
MIN_COVERAGE = 0.30

# The U-shape floor: below it, evidence of usability is required (or triage vouched).
NEGLECTED_USER_COUNT = 5

# Maximum territories explored per (field_code, operator_family) pair
MAX_TERRITORIES_PER_FIELD_OP = 3

DEFAULT_SIMS_PER_TERRITORY = 49
SURFACE_SIZE = 49
MIN_VIABLE_CAMPAIGN_BUDGET = 49
MIN_VIABLE_TERRITORY_SIMS = 49


@dataclass
class DatasetStat:
    dataset_code: str
    name: str
    field_count: int
    avg_user_count: float
    tried: int
    passed: int

    @property
    def hit_rate(self) -> float | None:
        return (self.passed / self.tried) if self.tried else None

    @property
    def crowding_score(self) -> float:
        """1.0 = pristine, 0.0 = saturated."""
        if self.avg_user_count <= 0:
            return 1.0
        return max(0.0, 1.0 - min(1.0, self.avg_user_count / CROWDED_USER_COUNT))


@dataclass
class Suggestion:
    field_code: str
    dataset_code: str
    operator_family: str = "ts_zscore"
    wrapper_shape: str | None = "rank"
    horizon_band: str = "medium"
    denominator: str | None = "cap"
    reason: str = ""
    user_count: int | None = None
    coverage: float | None = None
    posterior_score: float | None = None
    binding_constraint: str | None = None
    self_corr_headroom: float | None = None
    quartile: int | None = None


@dataclass
class AllocationTask:
    arm: str  # 'exploit' | 'random_stratified' | 'plateau_fill'
    field_code: str
    dataset_code: str
    operator_family: str
    wrapper_shape: str | None
    horizon_band: str
    denominator: str | None
    target_simulations: int
    reason: str
    quartile: int | None = None
    binding_constraint: str | None = None
    posterior_score: float | None = None


@dataclass
class BudgetPlan:
    total_simulations: int
    exploit_simulations: int
    random_stratified_simulations: int
    plateau_fill_simulations: int
    tasks: list[AllocationTask]
    quartile_boundaries: list[float] | None = None
    seed: int | None = None


# ----------------------------------------------------------------------
# Backward Compatibility: DiscountedThompsonSampler & SimulationBudgetOrchestrator
# ----------------------------------------------------------------------

# Re-exported for callers that still import them from this module. Unused here by
# design, so the lint waiver is the point rather than an oversight.
from app.services.allocator_bandit import (  # noqa: F401
    BanditArm,
    BudgetAllocation,
    DiscountedThompsonSampler,
    SimulationBudgetOrchestrator,
)

# ----------------------------------------------------------------------
# Dataset Statistics & Ranking
# ----------------------------------------------------------------------

def dataset_stats(
    db: Session,
    *,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
) -> list[DatasetStat]:
    """Per-dataset crowding and measured hit-rate.

    Filters field_to_dataset by region, delay, and universe (R10) to avoid cross-attribution.
    """
    rows = db.execute(
        select(
            Dataset.dataset_code,
            Dataset.name,
            func.count(DataField.id),
            func.avg(DataField.user_count),
        )
        .join(DataField, DataField.dataset_id == Dataset.id)
        .where(
            Dataset.region == region,
            Dataset.delay == delay,
            Dataset.universe == universe,
            DataField.region == region,
            DataField.delay == delay,
            DataField.universe == universe,
        )
        .group_by(Dataset.dataset_code, Dataset.name)
    ).all()

    field_to_dataset = dict(
        db.execute(
            select(DataField.field_code, Dataset.dataset_code)
            .join(Dataset, DataField.dataset_id == Dataset.id)
            .where(
                DataField.region == region,
                DataField.delay == delay,
                DataField.universe == universe,
            )
        ).all()
    )

    tried: dict[str, int] = {}
    passed: dict[str, int] = {}
    for family_key, passed_flag in db.execute(
        select(Alpha.family_key, AlphaMetric.passed_all_checks)
        .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(
            Alpha.family_key.is_not(None),
            Alpha.region == region,
            Alpha.delay == delay,
            Alpha.universe == universe,
        )
    ).all():
        ds = field_to_dataset.get(family_field_code(str(family_key)))
        if not ds:
            continue
        tried[ds] = tried.get(ds, 0) + 1
        if passed_flag:
            passed[ds] = passed.get(ds, 0) + 1

    return [
        DatasetStat(
            dataset_code=code,
            name=name,
            field_count=n,
            avg_user_count=float(avg or 0.0),
            tried=tried.get(code, 0),
            passed=passed.get(code, 0),
        )
        for code, name, n, avg in rows
    ]


def _dataset_priority(stat: DatasetStat) -> float:
    """Rank datasets. Crowding dominates until real evidence accumulates."""
    hit = stat.hit_rate
    if hit is None:
        return stat.crowding_score
    return 0.6 * min(1.0, hit * 10) + 0.4 * stat.crowding_score


# ----------------------------------------------------------------------
# Suggestion & Gated Exploitation (W3, W4, F4, F5, F9)
# ----------------------------------------------------------------------

def suggest(
    db: Session,
    *,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    n: int = 5,
    denominator: str | None = "cap",
    rng: random.Random | None = None,
    seed: int | None = None,
) -> list[Suggestion]:
    """Propose the next ``n`` coordinates (field, operator, wrapper, horizon) to build families on.

    Enforces hard diversity constraints (constrain-then-rank) and coordinate diversification.
    Unseeded calls use random.Random() for diverse interactive UI recommendations; reproducible
    campaigns pass an explicit seed or RNG instance.
    """
    _rng = rng or (random.Random(seed) if seed is not None else random.Random())

    stats = sorted(
        dataset_stats(db, region=region, delay=delay, universe=universe),
        key=_dataset_priority,
        reverse=True,
    )
    if not stats:
        return []

    # Effective per-dataset cap: for n <= 5, allows at least 1; for larger n, enforces 20%
    per_dataset_cap = max(1, math.ceil(n * MAX_DATASET_SHARE))

    # Query existing alpha territory distribution and submitted alphas
    existing_alphas = db.execute(
        select(Alpha.id, Alpha.family_key, Alpha.feature_json, Alpha.status)
        .where(
            Alpha.family_key.is_not(None),
            Alpha.region == region,
            Alpha.delay == delay,
            Alpha.universe == universe,
        )
    ).all()

    # Track mined (field_code, operator_family) pairs count, operators tried, and submitted territory signatures
    field_op_counts: dict[tuple[str, str], int] = {}
    field_ops_tried: dict[str, set[str]] = {}
    submitted_sigs: list[TerritorySignature] = []

    for aid, fkey, feat, status in existing_alphas:
        fcode = family_field_code(str(fkey))
        grid = (feat or {}).get("grid") or {}
        op = grid.get("ts") or "ts_zscore"
        field_op_counts[(fcode, op)] = field_op_counts.get((fcode, op), 0) + 1
        field_ops_tried.setdefault(fcode, set()).add(op)
        if status == "submitted" and fkey:
            submitted_sigs.append(
                parse_territory_signature(
                    str(fkey),
                    default_region=region,
                    default_universe=universe,
                    default_delay=delay,
                )
            )

    # Add confirmed submission attempts
    sub_attempts = db.execute(
        select(Alpha.family_key)
        .join(SubmissionAttempt, SubmissionAttempt.alpha_id == Alpha.id)
        .where(SubmissionAttempt.result == "submitted")
    ).all()
    for (fkey,) in sub_attempts:
        if fkey:
            submitted_sigs.append(
                parse_territory_signature(
                    str(fkey),
                    default_region=region,
                    default_universe=universe,
                    default_delay=delay,
                )
            )

    def is_territory_submitted(sig_field: str, sig_op: str, sig_horizon: str) -> bool:
        """Check if territory is excluded due to an existing submitted alpha (FF1).

        - Legacy key (horizon_band is None): sweeps all windows -> excludes all 3 horizons for (field, op).
        - Canonical key (horizon_band set): excludes specifically that horizon.
        """
        for s in submitted_sigs:
            if s.field_code != sig_field or s.region != region or s.universe != universe or s.delay != delay:
                continue
            if s.operator_family == sig_op:
                if s.horizon_band is None:
                    return True
                if s.horizon_band == sig_horizon:
                    return True
        return False

    out: list[Suggestion] = []
    dataset_suggest_count: dict[str, int] = {}
    used_territory_keys: set[str] = set()

    horizon_options = ["short", "medium", "long"]
    wrapper_options = ["rank", "zscore", "normalize", None]

    # Prioritize untried datasets first (Forced exploration guarantee)
    untried_datasets = [s for s in stats if s.tried == 0]
    tried_datasets = [s for s in stats if s.tried > 0]
    ordered_datasets = untried_datasets + tried_datasets

    for stat in ordered_datasets:
        if len(out) >= n:
            break
        if dataset_suggest_count.get(stat.dataset_code, 0) >= per_dataset_cap:
            continue

        ds_row = db.execute(
            select(Dataset).where(
                Dataset.dataset_code == stat.dataset_code,
                Dataset.region == region,
                Dataset.delay == delay,
                Dataset.universe == universe,
            )
        ).scalar_one_or_none()
        if ds_row is None:
            continue

        # Feasibility filters on DataField
        base_filters = [
            DataField.dataset_id == ds_row.id,
            DataField.field_type == "MATRIX",
            DataField.coverage.is_not(None),
            DataField.coverage >= MIN_COVERAGE,
        ]

        # Crowding band ceiling and floor for exploit path
        candidates = (
            db.execute(
                select(DataField)
                .where(
                    *base_filters,
                    or_(
                        DataField.classification_confidence > 0.5,
                        and_(
                            DataField.classification_confidence.is_(None),
                            DataField.user_count >= NEGLECTED_USER_COUNT,
                            DataField.user_count <= CROWDED_USER_COUNT,
                        ),
                    ),
                )
                .order_by(
                    DataField.classification_confidence.desc().nulls_last(),
                    DataField.user_count.asc().nulls_last(),
                    DataField.coverage.desc(),
                )
            )
            .scalars()
            .all()
        )

        if not candidates:
            log.info(
                "allocator_dataset_no_candidates",
                dataset=stat.dataset_code,
                reason="all_fields_exceed_crowding_ceiling_or_insufficient_coverage",
            )

        for f in candidates:
            if len(out) >= n:
                break
            if dataset_suggest_count.get(stat.dataset_code, 0) >= per_dataset_cap:
                break

            # Find an eligible (operator, horizon) pair not submitted and not capped
            # Invariant (F4 / FF1): Exclusion is keyed on territory (field, op, horizon), never the whole field
            tried_for_field = field_ops_tried.get(f.field_code, set())
            
            chosen_op: str | None = None
            chosen_horizon: str | None = None
            chosen_tkey: str | None = None

            # Try candidate operators ordered by: rotated untried on this field first, then rotated standard transforms
            op_idx = len(out) % len(DEFAULT_TS_TRANSFORMS)
            rotated_ops = list(DEFAULT_TS_TRANSFORMS[op_idx:]) + list(DEFAULT_TS_TRANSFORMS[:op_idx])
            candidate_ops = [op for op in rotated_ops if op not in tried_for_field] + [
                op for op in rotated_ops if op in tried_for_field
            ]

            for op in candidate_ops:
                # Check per-(field, op) saturation cap
                if field_op_counts.get((f.field_code, op), 0) >= MAX_TERRITORIES_PER_FIELD_OP:
                    continue
                
                # Check horizons for an unsubmitted, unused territory
                for h_idx, horizon in enumerate(horizon_options):
                    tkey = canonical_territory_key(
                        f.field_code, op, horizon, region, universe, delay
                    )
                    if tkey in used_territory_keys or is_territory_submitted(f.field_code, op, horizon):
                        continue
                    
                    chosen_op = op
                    chosen_horizon = horizon
                    chosen_tkey = tkey
                    break
                
                if chosen_op is not None:
                    break

            if chosen_op is None or chosen_horizon is None or chosen_tkey is None:
                # All operator-horizon territories for this field are saturated or submitted
                continue

            chosen_wrap = wrapper_options[len(out) % len(wrapper_options)]
            used_territory_keys.add(chosen_tkey)
            dataset_suggest_count[stat.dataset_code] = dataset_suggest_count.get(stat.dataset_code, 0) + 1

            hit = stat.hit_rate
            reason = (
                f"{stat.dataset_code}: {f.user_count or 0} users, "
                f"coverage {f.coverage:.2f}, "
                + (f"hit-rate {hit:.1%}" if hit is not None else "dataset unexplored")
            )

            out.append(
                Suggestion(
                    field_code=f.field_code,
                    dataset_code=stat.dataset_code,
                    operator_family=chosen_op,
                    wrapper_shape=chosen_wrap,
                    horizon_band=chosen_horizon,
                    denominator=denominator,
                    reason=reason,
                    user_count=f.user_count,
                    coverage=f.coverage,
                    posterior_score=None,
                    binding_constraint=f"dataset_cap({per_dataset_cap})" if dataset_suggest_count[stat.dataset_code] >= per_dataset_cap else None,
                    self_corr_headroom=None,
                )
            )

    log.info("allocator_suggested", n=len(out), datasets=len(dataset_suggest_count))
    return out


# ----------------------------------------------------------------------
# 3-Arm Budget Allocation & Arithmetic Closure (W1, W2, W4)
# ----------------------------------------------------------------------

def plan_budget_allocation(
    db: Session,
    total_simulations: int = 200,
    *,
    arms: dict[str, float] | None = None,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    sims_per_territory: int = DEFAULT_SIMS_PER_TERRITORY,
    seed: int | None = None,
) -> BudgetPlan:
    """Computes a 3-way budget allocation across exploit (50%), random stratified (30%), and plateau fill (20%).

    Guarantees:
    1. Exact Arithmetic Closure: sum of task targets == total_simulations for any positive budget.
    2. Whole-surface territory granularity (expansion always operates on whole surfaces).
    3. Reproducible seeding via explicit Random instance.
    """
    if total_simulations <= 0:
        return BudgetPlan(
            total_simulations=0,
            exploit_simulations=0,
            random_stratified_simulations=0,
            plateau_fill_simulations=0,
            tasks=[],
            quartile_boundaries=[],
            seed=seed,
        )

    rng = random.Random(seed)

    has_cap = (
        db.execute(
            select(DataField.id).where(DataField.field_code == "cap", DataField.region == region)
        ).scalars().first()
        is not None
    )
    default_denom = "cap" if has_cap else None

    # Query existing simulated counts by family to find incomplete surfaces (joined to AlphaMetric)
    sim_counts = dict(
        db.execute(
            select(Alpha.family_key, func.count(distinct(AlphaMetric.alpha_id)))
            .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
            .where(
                Alpha.family_key.is_not(None),
                Alpha.region == region,
                Alpha.delay == delay,
                Alpha.universe == universe,
            )
            .group_by(Alpha.family_key)
        ).all()
    )
    valid_matrix_fields = set(
        db.execute(
            select(DataField.field_code)
            .where(
                DataField.region == region,
                DataField.delay == delay,
                DataField.field_type == "MATRIX",
            )
        ).scalars().all()
    )
    incomplete_families = [
        fkey for fkey, count in sim_counts.items()
        if 0 < count < sims_per_territory and family_field_code(str(fkey)) in valid_matrix_fields
    ]

    arm_shares = arms or {"exploit": 0.50, "random_stratified": 0.30, "plateau_fill": 0.20}
    declared_exploit = int(total_simulations * arm_shares.get("exploit", 0.50))
    declared_random = int(total_simulations * arm_shares.get("random_stratified", 0.30))
    declared_plateau = total_simulations - declared_exploit - declared_random

    if not incomplete_families:
        exploit_share = arm_shares.get("exploit", 0.50) + arm_shares.get("plateau_fill", 0.20)
        random_share = arm_shares.get("random_stratified", 0.30)
        fill_share = 0.0
    else:
        exploit_share = arm_shares.get("exploit", 0.50)
        random_share = arm_shares.get("random_stratified", 0.30)
        fill_share = arm_shares.get("plateau_fill", 0.20)

    exploit_budget = int(total_simulations * exploit_share)
    random_budget = int(total_simulations * random_share)
    plateau_budget = total_simulations - exploit_budget - random_budget

    tasks: list[AllocationTask] = []
    used_territory_keys: set[str] = set()

    # ------------------------------------------------------------------
    # 1. Exploit Arm
    # ------------------------------------------------------------------
    suggestions: list[Suggestion] = []
    n_exploit_territories = max(1, exploit_budget // sims_per_territory) if total_simulations >= (2 * sims_per_territory) else 1
    suggestions = suggest(
        db,
        region=region,
        delay=delay,
        universe=universe,
        n=n_exploit_territories,
        denominator=default_denom,
        rng=rng,
    )
    # The exploit arm is hunting, not measuring, so skipping fields that have never
    # produced a book costs nothing scientifically and saves slots that would re-learn a
    # known answer. Two such fields had 686 alphas queued behind them.
    dead = dead_field_codes(db)
    if dead:
        kept = [s for s in suggestions if s.field_code not in dead]
        if len(kept) != len(suggestions):
            log.info(
                "exploit_arm_skipped_dead_fields",
                skipped=len(suggestions) - len(kept),
                fields=sorted(dead),
            )
        suggestions = kept

    for s in suggestions:
        tkey = canonical_territory_key(
            s.field_code, s.operator_family, s.horizon_band, region, universe, delay
        )
        used_territory_keys.add(tkey)
        tasks.append(
            AllocationTask(
                arm="exploit",
                field_code=s.field_code,
                dataset_code=s.dataset_code,
                operator_family=s.operator_family,
                wrapper_shape=s.wrapper_shape,
                horizon_band=s.horizon_band,
                denominator=s.denominator,
                target_simulations=min(total_simulations, sims_per_territory),
                reason=f"Exploit uncrowded research territory: {s.reason}",
                posterior_score=None,
            )
        )

    # ------------------------------------------------------------------
    # 2. Random Stratified Arm — 4 Crowding Quartiles (Protocol v2)
    # ------------------------------------------------------------------
    all_fields = db.execute(
        select(DataField.field_code, DataField.user_count, Dataset.dataset_code)
        .join(Dataset, DataField.dataset_id == Dataset.id)
        .where(
            DataField.region == region,
            DataField.delay == delay,
            DataField.universe == universe,
            DataField.field_type == "MATRIX",
        )
    ).all()

    # DELIBERATELY NOT filtered for dead fields. An earlier version excluded them here
    # and that was wrong, for a reason worth recording so it is not re-introduced.
    #
    # Every dead field we have found sits in the LOWEST crowding quartile (user counts
    # 1, 5, 7, 8). Dropping them would preferentially thin the uncrowded end of the
    # sample — a bias in precisely the dimension the Phase 2 validation study measures,
    # in the one arm CLAUDE.md says must stay unbiased.
    #
    # It would also hide a possible real result: uncrowded fields may be uncrowded
    # *because* their data is unusable. "Low-crowding territory yields nothing" is a
    # finding about crowding, not noise to filter away.
    #
    # The waste is real but belongs elsewhere: the exploit arm skips dead fields, since
    # that arm is hunting rather than measuring and loses no scientific value by doing so.

    quartile_boundaries: list[float] | None = None
    if all_fields and total_simulations >= (2 * sims_per_territory):
        user_counts = [f[1] for f in all_fields if f[1] is not None]
        if user_counts:
            q_bounds = [
                float(np.percentile(user_counts, 25)),
                float(np.percentile(user_counts, 50)),
                float(np.percentile(user_counts, 75)),
            ]
        else:
            q_bounds = [10.0, 100.0, 1000.0]
        quartile_boundaries = q_bounds

        # Partition into 4 quartiles
        q_fields: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
        for f in all_fields:
            uc = f[1] or 0
            if uc <= q_bounds[0]:
                q_fields[1].append(f)
            elif uc <= q_bounds[1]:
                q_fields[2].append(f)
            elif uc <= q_bounds[2]:
                q_fields[3].append(f)
            else:
                q_fields[4].append(f)

        n_rand_territories = max(1, random_budget // sims_per_territory)
        horizon_choices = ["short", "medium", "long"]
        wrapper_choices = [cs for cs in DEFAULT_CROSS_SECTION if cs is not None]

        for i in range(n_rand_territories):
            quartile_idx = (i % 4) + 1
            pool = q_fields[quartile_idx] or all_fields
            
            # Re-draw on collision with exploit territories
            chosen_field, chosen_uc, chosen_ds = rng.choice(pool)
            op = rng.choice(DEFAULT_TS_TRANSFORMS)
            wrap = rng.choice(wrapper_choices)
            horizon = rng.choice(horizon_choices)

            tkey = canonical_territory_key(chosen_field, op, horizon, region, universe, delay)
            retries = 0
            while tkey in used_territory_keys and retries < 10:
                chosen_field, chosen_uc, chosen_ds = rng.choice(pool)
                op = rng.choice(DEFAULT_TS_TRANSFORMS)
                horizon = rng.choice(horizon_choices)
                tkey = canonical_territory_key(chosen_field, op, horizon, region, universe, delay)
                retries += 1

            used_territory_keys.add(tkey)
            tasks.append(
                AllocationTask(
                    arm="random_stratified",
                    field_code=chosen_field,
                    dataset_code=chosen_ds,
                    operator_family=op,
                    wrapper_shape=wrap,
                    horizon_band=horizon,
                    denominator=default_denom,
                    target_simulations=sims_per_territory,
                    reason=f"🔬 Calibration — expected to fail, required for validation study (Q{quartile_idx} quartile, ~{chosen_uc or 0} users)",
                    quartile=quartile_idx,
                )
            )

    # ------------------------------------------------------------------
    # 3. Plateau Fill Arm (requests full surface; create_alpha dedupes)
    # ------------------------------------------------------------------
    if plateau_budget > 0 and incomplete_families:
        field_to_dataset = dict(
            db.execute(
                select(DataField.field_code, Dataset.dataset_code)
                .join(Dataset, DataField.dataset_id == Dataset.id)
                .where(DataField.region == region, DataField.delay == delay)
            ).all()
        )

        for fkey in incomplete_families:
            count = sim_counts.get(fkey, 0)
            fcode = family_field_code(str(fkey))
            dscode = field_to_dataset.get(fcode, "fundamentals")
            tasks.append(
                AllocationTask(
                    arm="plateau_fill",
                    field_code=fcode,
                    dataset_code=dscode,
                    operator_family="ts_zscore",
                    wrapper_shape="rank",
                    horizon_band="medium",
                    denominator=default_denom,
                    target_simulations=sims_per_territory,
                    reason=f"Complete surface for promising family {fkey} ({count}/{sims_per_territory} simulated)",
                )
            )
            break

    # ------------------------------------------------------------------
    # Exact Arithmetic Closure (R2)
    # ------------------------------------------------------------------
    if not tasks:
        fallback_field = all_fields[0][0] if all_fields else "close"
        fallback_ds = all_fields[0][2] if all_fields else "pv1"
        tasks.append(
            AllocationTask(
                arm="exploit",
                field_code=fallback_field,
                dataset_code=fallback_ds,
                operator_family="ts_zscore",
                wrapper_shape="rank",
                horizon_band="medium",
                denominator=default_denom,
                target_simulations=total_simulations,
                reason="Default baseline allocation task",
            )
        )

    current_total = sum(t.target_simulations for t in tasks)
    remainder = total_simulations - current_total

    if remainder > 0:
        for i in range(remainder):
            tasks[i % len(tasks)].target_simulations += 1
    elif remainder < 0:
        excess = -remainder
        for t in sorted(tasks, key=lambda x: x.target_simulations, reverse=True):
            if excess <= 0:
                break
            can_reduce = max(0, t.target_simulations - 1)
            reduction = min(excess, can_reduce)
            t.target_simulations -= reduction
            excess -= reduction
        if excess > 0:
            for t in tasks:
                if excess <= 0:
                    break
                can_reduce = max(0, t.target_simulations - 1)
                reduction = min(excess, can_reduce)
                t.target_simulations -= reduction
                excess -= reduction

    return BudgetPlan(
        total_simulations=total_simulations,
        exploit_simulations=declared_exploit,
        random_stratified_simulations=declared_random,
        plateau_fill_simulations=declared_plateau,
        tasks=tasks,
        quartile_boundaries=quartile_boundaries,
        seed=seed,
    )
