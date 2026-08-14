"""The allocator must refuse to exploit (STRATEGY.md §6).

A greedy bandit finds the best dataset and pours everything into it. Here that is
the wrong answer: concentrating produces mutually-correlated alphas, and BRAIN
pays only for uncorrelated ones, so the extra output is worth nothing. These
tests pin the diversity guarantees, because they are the part a well-meaning
"optimisation" would quietly remove.
"""

from __future__ import annotations

from app.models.fields import DataField, Dataset
from app.services.allocator import MAX_DATASET_SHARE, DatasetStat, _dataset_priority, suggest


def _dataset(db, code: str, *, n_fields: int, users: int) -> Dataset:
    ds = Dataset(
        dataset_code=code,
        name=code,
        region="USA",
        delay=1,
        universe="TOP3000",
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
                universe="TOP3000",
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
    cap = max(1, int(n * MAX_DATASET_SHARE))
    assert all(c <= cap for c in per_dataset.values()), per_dataset


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
