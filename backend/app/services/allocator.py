"""Stage 5 — deciding what to try next (STRATEGY.md §6).

This is what turns the pipeline from a tool you drive into a machine that runs.
It answers one question without the operator: *which field should the next
family be built on?*

The counterintuitive part, and the reason a textbook bandit is wrong here: **it
must refuse to exploit.** A greedy allocator finds the best dataset and pours
everything into it, which produces a pile of mutually-correlated alphas — and
BRAIN pays only for *uncorrelated* ones, so most of that output is worth
nothing. Portfolio diversity beats marginal Sharpe.

Three mechanisms enforce that:

* ``MAX_DATASET_SHARE`` caps any single dataset's slice of the budget.
* Untried datasets are always granted a slot before ranking begins, so a dataset
  can never be written off on zero evidence.
* Fields are scored on crowding (``user_count``/``alpha_count``) as much as on
  measured hit-rate, because an uncrowded field is where un-arbitraged signal
  survives — the whole premise of Rule 1.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.fields import DataField, Dataset
from app.models.results import AlphaMetric
from app.services.plateau import family_field_code

log = structlog.get_logger("allocator")

# No dataset may take more than this share of a batch, however well it scores.
MAX_DATASET_SHARE = 0.20

# Fields this heavily used are effectively fully arbitraged. pv1 averages ~18,485
# users/field; fundamental2 and news12 average ~130 and ~109 respectively, which
# is the gap this threshold is drawn to separate.
CROWDED_USER_COUNT = 2_000

# Below this coverage a field is too sparse to build a stable signal on.
MIN_COVERAGE = 0.30

# The U-shape. "Fewer users = better" is only true in the middle of the range.
# Measured on the real catalog: 1,238 fields have <10 users and average 5 alphas
# each — that tail is dominated by metadata (estimate *counts*, guidance
# envelopes, membership flags), not by undiscovered edge. Sorting purely by
# ascending user_count walks straight into it, which is how the allocator came to
# recommend `top2000`, a universe-membership indicator.
#
# So a floor as well as a ceiling: below it, some evidence that a human found the
# field usable is required, unless LLM triage has vouched for it.
NEGLECTED_USER_COUNT = 5


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
    denominator: str | None
    reason: str
    user_count: int | None
    coverage: float | None


def dataset_stats(db: Session, *, region: str, delay: int, universe: str) -> list[DatasetStat]:
    """Per-dataset crowding and measured hit-rate.

    Hit-rate is attributed by matching a family_key's field back to its dataset,
    which is exactly why the constructor writes ``family_key`` on every alpha.
    """
    rows = db.execute(
        select(
            Dataset.dataset_code,
            Dataset.name,
            func.count(DataField.id),
            func.avg(DataField.user_count),
        )
        .join(DataField, DataField.dataset_id == Dataset.id)
        .where(Dataset.region == region, Dataset.delay == delay, Dataset.universe == universe)
        .group_by(Dataset.dataset_code, Dataset.name)
    ).all()

    field_to_dataset = dict(
        db.execute(
            select(DataField.field_code, Dataset.dataset_code)
            .join(Dataset, DataField.dataset_id == Dataset.id)
            .where(DataField.region == region, DataField.delay == delay)
        ).all()
    )

    tried: dict[str, int] = {}
    passed: dict[str, int] = {}
    for family_key, passed_flag in db.execute(
        select(Alpha.family_key, AlphaMetric.passed_all_checks)
        .join(AlphaMetric, AlphaMetric.alpha_id == Alpha.id)
        .where(Alpha.family_key.is_not(None))
    ).all():
        # Strip the "@region/universe/dN" suffix before taking the field code —
        # see family_field_code() for why splitting on "/" alone is wrong.
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
        # Unexplored: judged purely on how un-mined it is.
        return stat.crowding_score
    # Evidence blended with crowding — a proven dataset still loses ground as it
    # fills up, which is what keeps the search moving.
    return 0.6 * min(1.0, hit * 10) + 0.4 * stat.crowding_score


def suggest(
    db: Session,
    *,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    n: int = 5,
    denominator: str | None = "cap",
) -> list[Suggestion]:
    """Propose the next ``n`` fields to build families on."""
    stats = sorted(
        dataset_stats(db, region=region, delay=delay, universe=universe),
        key=_dataset_priority,
        reverse=True,
    )
    if not stats:
        return []

    per_dataset_cap = max(1, int(n * MAX_DATASET_SHARE))
    used = {
        family_field_code(str(k))
        for (k,) in db.execute(select(Alpha.family_key).where(Alpha.family_key.is_not(None))).all()
    }

    out: list[Suggestion] = []
    for stat in stats:
        if len(out) >= n:
            break
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

        # Prefer LLM-vouched fields (classification_confidence > 0.5). Where
        # triage has not run, fall back to the U-shaped band: usable evidence
        # exists, but the field is not yet crowded.
        base_filters = [
            DataField.dataset_id == ds_row.id,
            DataField.field_type == "MATRIX",
            DataField.coverage.is_not(None),
            DataField.coverage >= MIN_COVERAGE,
        ]
        if used:
            base_filters.append(DataField.field_code.not_in(used))

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
                        ),
                    ),
                )
                # Triaged-usable first, then least crowded within the safe band.
                .order_by(
                    DataField.classification_confidence.desc().nulls_last(),
                    DataField.user_count.asc().nulls_last(),
                    DataField.coverage.desc(),
                )
                .limit(per_dataset_cap)
            )
            .scalars()
            .all()
        )

        for f in candidates:
            if len(out) >= n:
                break
            hit = stat.hit_rate
            reason = (
                f"{stat.dataset_code}: {f.user_count or 0} users, "
                f"coverage {f.coverage:.2f}, "
                + (f"dataset hit-rate {hit:.1%}" if hit is not None else "dataset unexplored")
            )
            out.append(
                Suggestion(
                    field_code=f.field_code,
                    dataset_code=stat.dataset_code,
                    denominator=denominator,
                    reason=reason,
                    user_count=f.user_count,
                    coverage=f.coverage,
                )
            )
            used.add(f.field_code)

    log.info("allocator_suggested", n=len(out), datasets=len({s.dataset_code for s in out}))
    return out
