"""Empty-book guard and synthetic-data detection.

Both defences exist because of what the 332-point audit found: 44 simulations spent on
fields that produced no portfolio at all, and 147 fixture rows sitting in the production
database with metrics that violate BRAIN's own fitness formula.
"""

from __future__ import annotations

import math

import pytest
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.fields import DataField, Dataset
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.result_import import held_no_positions, import_result, parse_result_block
from scripts.purge_synthetic_alphas import classify


def _seed_field(db: Session, code: str) -> str:
    ds = db.query(Dataset).filter_by(dataset_code="hygiene_ds").first()
    if not ds:
        ds = Dataset(
            dataset_code="hygiene_ds", name="hygiene",
            region="USA", universe="TOP3000", delay=1,
        )
        db.add(ds)
        db.flush()
    if not db.query(DataField).filter_by(field_code=code).first():
        db.add(DataField(dataset_id=ds.id, field_code=code, field_type="MATRIX", user_count=1))
        db.flush()
    return code


def _payload(**over) -> dict:
    """A flat result block — the shape ``parse_result_block`` consumes."""
    base = {
        "sharpe": 1.50, "fitness": 1.10, "turnover": 0.30, "returns": 0.12,
        "longCount": 900, "shortCount": 890, "drawdown": 0.05,
        "checks": [
            {"name": "LOW_SHARPE", "result": "PASS", "value": 1.50, "limit": 1.25},
            {"name": "LOW_FITNESS", "result": "PASS", "value": 1.10, "limit": 1.0},
        ],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Empty-book guard
# ---------------------------------------------------------------------------


def test_empty_book_is_detected() -> None:
    empty = parse_result_block(_payload(longCount=0, shortCount=0, sharpe=0.0, returns=0.0, turnover=0.0))
    traded = parse_result_block(_payload())
    assert held_no_positions(empty)
    assert not held_no_positions(traded)


def test_empty_book_is_not_recorded_as_rejected(db_session: Session) -> None:
    """An absent alpha is not a bad alpha.

    Filing it as `rejected` would put it in the denominator of every hit rate next to
    alphas that genuinely traded and lost.
    """
    code = _seed_field(db_session, "f_empty_book")
    alpha = create_alpha(
        db_session, f"rank(ts_zscore({code},20))",
        AlphaSettings(region="USA", universe="TOP3000", delay=1),
        family_key="hygiene/empty", source="test",
    ).alpha
    db_session.flush()

    res = import_result(
        db_session, alpha,
        _payload(longCount=0, shortCount=0, sharpe=0.0, returns=0.0, turnover=0.0),
    )

    assert res.new_status == AlphaStatus.ARCHIVED.value
    assert res.new_status != AlphaStatus.REJECTED.value


def test_traded_alpha_still_classifies_normally(db_session: Session) -> None:
    """The guard must not change the verdict for an alpha that actually held a book."""
    code = _seed_field(db_session, "f_traded")
    alpha = create_alpha(
        db_session, f"rank(ts_zscore({code},20))",
        AlphaSettings(region="USA", universe="TOP3000", delay=1),
        family_key="hygiene/traded", source="test",
    ).alpha
    db_session.flush()

    res = import_result(db_session, alpha, _payload())
    assert res.new_status == AlphaStatus.PASSED.value


def test_missing_counts_are_not_treated_as_empty() -> None:
    """Older rows have no long_count at all. Absent is not zero."""
    parsed = parse_result_block({"is": {"sharpe": 1.5, "fitness": 1.1, "turnover": 0.3, "returns": 0.12}})
    assert not held_no_positions(parsed)


# ---------------------------------------------------------------------------
# Synthetic-data detection
# ---------------------------------------------------------------------------


def _add_family(db: Session, family: str, rows: list[dict], code: str, offset: int = 0) -> None:
    """Seed one family. `offset` shifts the windows so two families on the same field
    produce distinct expressions — create_alpha dedupes on expression hash, so without
    it the second family's rows are silently dropped as duplicates."""
    for i, r in enumerate(rows):
        alpha = create_alpha(
            db, f"rank(ts_zscore({code},{5 + offset + i}))",
            AlphaSettings(region="USA", universe="TOP3000", delay=1),
            family_key=family, grid={"window": 5 + i, "decay": 0}, source="test",
        ).alpha
        db.flush()
        import_result(db, alpha, _payload(**r))


def test_fabricated_family_is_flagged(db_session: Session) -> None:
    """Identical metrics that also violate the fitness formula = fixture data."""
    code = _seed_field(db_session, "f_fabricated")
    # 0.50 * sqrt(0.15/0.30) = 0.35, but 1.20 is claimed — exactly the pattern found.
    rows = [
        {"sharpe": 0.50, "fitness": 1.20, "turnover": 0.30, "returns": 0.15,
         "longCount": 500, "shortCount": 500}
    ] * 8
    _add_family(db_session, "fake/family", rows, code)
    db_session.flush()

    verdict = next(v for v in classify(db_session) if v.family_key == "fake/family")
    assert verdict.kind == "fabricated"
    assert "violate BRAIN's own fitness formula" in verdict.evidence


def test_real_flat_surface_is_not_flagged(db_session: Session) -> None:
    """The critical negative: a genuinely flat surface must survive.

    Detection needs *both* signals — repeated metrics AND a formula violation. A real
    family whose points happen to score alike is not fabricated, and deleting one would
    be far worse than leaving a fixture in place.
    """
    code = _seed_field(db_session, "f_flat_real")
    sharpe, turnover, returns = 1.40, 0.30, 0.12
    fitness = sharpe * math.sqrt(returns / max(turnover, 0.125))
    rows = [
        {"sharpe": sharpe, "fitness": round(fitness, 4), "turnover": turnover,
         "returns": returns, "longCount": 800, "shortCount": 800}
    ] * 8
    _add_family(db_session, "real/flat", rows, code)
    db_session.flush()

    verdict = next(v for v in classify(db_session) if v.family_key == "real/flat")
    assert verdict.kind == "clean", verdict.evidence


def test_empty_book_family_is_flagged_separately(db_session: Session) -> None:
    code = _seed_field(db_session, "f_deadfield")
    rows = [
        {"sharpe": 0.0, "fitness": 0.0, "turnover": 0.0, "returns": 0.0,
         "longCount": 0, "shortCount": 0}
    ] * 6
    _add_family(db_session, "dead/field", rows, code)
    db_session.flush()

    verdict = next(v for v in classify(db_session) if v.family_key == "dead/field")
    assert verdict.kind == "empty-book"
    assert "0 positions" in verdict.evidence


def test_varied_metrics_still_flagged_when_the_formula_is_violated(db_session: Session) -> None:
    """The false negative that escaped the first version of the detector.

    ``close/cap`` had 49 fabricated rows but 5 distinct metric tuples, one more than the
    diversity threshold allowed, so it was reported clean while violating the fitness
    formula on every single row. Diversity was only ever a proxy; the formula violation
    is the proof. BRAIN *computes* fitness with that formula, so genuine output cannot
    disagree with it systematically no matter how varied the numbers look.
    """
    code = _seed_field(db_session, "f_varied_fake")
    # Six distinct tuples — nothing repetitive — but every fitness is impossible.
    rows = [
        {"sharpe": s_, "fitness": 1.20, "turnover": 0.30, "returns": 0.15,
         "longCount": 500, "shortCount": 500}
        for s_ in (0.50, 0.55, 0.60, 2.55, 2.60, 2.80, 4.00, 0.52)
    ]
    _add_family(db_session, "varied/fake", rows, code)
    db_session.flush()

    verdict = next(v for v in classify(db_session) if v.family_key == "varied/fake")
    assert verdict.kind == "fabricated", verdict.evidence
    assert "distinct tuples" in verdict.evidence


def test_varied_real_metrics_stay_clean(db_session: Session) -> None:
    """The matching negative: varied metrics that DO satisfy the formula are real."""
    code = _seed_field(db_session, "f_varied_real")
    rows = []
    for sharpe, turnover, returns in (
        (1.40, 0.30, 0.12), (1.55, 0.42, 0.14), (1.20, 0.25, 0.09),
        (1.70, 0.55, 0.17), (0.95, 0.18, 0.07), (1.33, 0.36, 0.11),
    ):
        rows.append({
            "sharpe": sharpe, "turnover": turnover, "returns": returns,
            "fitness": round(sharpe * math.sqrt(returns / max(turnover, 0.125)), 4),
            "longCount": 800, "shortCount": 800,
        })
    _add_family(db_session, "varied/real", rows, code)
    db_session.flush()

    verdict = next(v for v in classify(db_session) if v.family_key == "varied/real")
    assert verdict.kind == "clean", verdict.evidence


# ---------------------------------------------------------------------------
# Dead-field detection (derived, never stored)
# ---------------------------------------------------------------------------


def test_field_dead_across_two_templates(db_session: Session) -> None:
    """Corroboration across templates is the cheap way to be sure."""
    from app.services.field_health import dead_field_codes, field_health

    code = _seed_field(db_session, "f_dead_health")
    empty = {"sharpe": 0.0, "fitness": 0.0, "turnover": 0.0, "returns": 0.0,
             "longCount": 0, "shortCount": 0}
    _add_family(db_session, "health/dead-a", [empty] * 2, code)
    _add_family(db_session, "health/dead-b", [empty] * 2, code, offset=50)
    db_session.flush()

    health = field_health(db_session)[code]
    assert health.is_dead
    assert "2 distinct templates" in health.evidence
    assert code in dead_field_codes(db_session)


def test_single_template_needs_a_long_run(db_session: Session) -> None:
    """A handful of empties from ONE template cannot distinguish a dead field from a
    broken template, and retiring on that basis would discard real territory."""
    from app.services.field_health import field_health

    code = _seed_field(db_session, "f_one_template")
    empty = {"sharpe": 0.0, "fitness": 0.0, "turnover": 0.0, "returns": 0.0,
             "longCount": 0, "shortCount": 0}
    _add_family(db_session, "health/one-template", [empty] * 5, code)
    db_session.flush()

    health = field_health(db_session)[code]
    assert not health.is_dead
    assert "failing template" in health.evidence


def test_field_that_ever_traded_is_not_dead(db_session: Session) -> None:
    """One empty result among real ones is an expression problem, not a dead field."""
    from app.services.field_health import field_health

    code = _seed_field(db_session, "f_live_health")
    rows = [
        {"sharpe": 0.0, "fitness": 0.0, "turnover": 0.0, "returns": 0.0,
         "longCount": 0, "shortCount": 0},
        {"sharpe": 1.4, "fitness": 1.1, "turnover": 0.3, "returns": 0.12,
         "longCount": 800, "shortCount": 800},
        {"sharpe": 1.2, "fitness": 1.0, "turnover": 0.25, "returns": 0.10,
         "longCount": 790, "shortCount": 780},
    ]
    _add_family(db_session, "health/live", rows, code)
    db_session.flush()

    assert not field_health(db_session)[code].is_dead


def test_too_few_attempts_is_not_enough_to_retire(db_session: Session) -> None:
    """Retiring a field on one or two empty results would discard real territory."""
    from app.services.field_health import MIN_EMPTY_SIMS_TO_RETIRE, field_health

    code = _seed_field(db_session, "f_thin_health")
    rows = [
        {"sharpe": 0.0, "fitness": 0.0, "turnover": 0.0, "returns": 0.0,
         "longCount": 0, "shortCount": 0}
    ] * (MIN_EMPTY_SIMS_TO_RETIRE - 1)
    _add_family(db_session, "health/thin", rows, code)
    db_session.flush()

    assert not field_health(db_session)[code].is_dead
