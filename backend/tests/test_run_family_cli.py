"""Smoke tests for the run_family CLI — the entry point of the whole loop.

This file exists because ``scripts/run_family.py`` crashed on *every* invocation
with ``AttributeError: 'Namespace' object has no attribute 'operator'`` while 280
tests passed: the suite exercised ``expand()`` directly and nothing ever imported
the CLI. A broken entry point is indistinguishable from a working one until someone
runs it by hand.

These tests drive ``main()`` through argparse the way a person would, with
``--simulate 0`` so nothing touches the network.
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.fields import DataField, Dataset


@pytest.fixture
def seeded_family(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> str:
    """A field the constructor can build a real family on, plus a denominator.

    The field name is unique per test. run_family commits, and the db_session
    fixture cannot roll a commit back, so a shared field code would let one test's
    alphas show up in the next test's family query.
    """
    ds = db_session.query(Dataset).filter_by(dataset_code="fundamental6").first()
    if not ds:
        ds = Dataset(
            dataset_code="fundamental6", name="Fundamental",
            region="USA", universe="TOP3000", delay=1,
        )
        db_session.add(ds)
        db_session.flush()

    field_code = f"liab_{abs(hash(request.node.name)) % 100000}"
    for code in (field_code, "cap"):
        if not db_session.query(DataField).filter_by(field_code=code).first():
            db_session.add(
                DataField(dataset_id=ds.id, field_code=code, field_type="MATRIX", user_count=1)
            )
    db_session.commit()

    # run_family opens its own sessions; point them at the test session's factory.
    import app.db as app_db
    import scripts.run_family as rf
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        yield db_session

    monkeypatch.setattr(rf, "session_scope", _scope)
    monkeypatch.setattr(app_db, "session_scope", _scope, raising=False)
    return field_code


def _run(argv: list[str]) -> int:
    import scripts.run_family as rf

    sys.argv = ["run_family", *argv]
    return rf.main()


def test_cli_parses_and_expands(seeded_family: str, db_session: Session, capsys) -> None:
    """The regression: this used to raise AttributeError before doing anything."""
    rc = _run(["--field", seeded_family, "--denominator", "cap", "--simulate", "0"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "expanded:" in out
    assert db_session.query(Alpha).filter(
        Alpha.family_key.like(f"{seeded_family}/cap%")
    ).count() > 0


def test_cli_plural_operator_and_wrapper_flags(seeded_family: str, capsys) -> None:
    """--operators/--wrappers were defined but read under singular names."""
    rc = _run([
        "--field", seeded_family, "--denominator", "cap",
        "--operators", "ts_zscore", "--wrappers", "rank", "--groups", "none",
        "--simulate", "0",
    ])
    assert rc == 0
    assert "expanded:" in capsys.readouterr().out


def test_cli_b2_truncation_probe_emits_three_levels(
    seeded_family: str, db_session: Session, capsys
) -> None:
    """The Stage-A probe from plan item B2: 3 truncation levels on the coarse grid.

    Pins the arithmetic the plan depends on — 3x3 grid x 3 settings combinations is
    27 simulations, not the 147 a full 3 x 7x7 sweep would cost.
    """
    rc = _run([
        "--field", seeded_family, "--denominator", "cap",
        "--grid", "coarse",
        "--truncations", "0.08", "0.04", "0.01",
        "--settings-per-structure", "3",
        "--operators", "ts_zscore", "--wrappers", "rank", "--groups", "none",
        "--neutralizations", "SUBINDUSTRY",
        "--simulate", "0",
    ])
    assert rc == 0

    emitted = (
        db_session.query(Alpha)
        .filter(Alpha.family_key.like(f"{seeded_family}/cap%"))
        .all()
    )
    levels = {a.truncation for a in emitted}
    assert levels == {0.08, 0.04, 0.01}, f"probe must sweep all three levels, got {levels}"

    # 3x3 coarse grid, one complete surface per truncation level.
    per_level = {lvl: sum(1 for a in emitted if a.truncation == lvl) for lvl in levels}
    assert all(count == 9 for count in per_level.values()), per_level
    assert sum(per_level.values()) == 27

    assert "truncation levels emitted" in capsys.readouterr().out


def test_cli_defaults_to_reference_truncation_only(seeded_family: str, db_session: Session) -> None:
    """Without --settings-per-structure, a run stays on the historical baseline."""
    rc = _run(["--field", seeded_family, "--denominator", "cap", "--simulate", "0"])
    assert rc == 0

    levels = {
        a.truncation
        for a in db_session.query(Alpha)
        .filter(Alpha.family_key.like(f"{seeded_family}/cap%"))
        .all()
    }
    assert levels == {0.08}
