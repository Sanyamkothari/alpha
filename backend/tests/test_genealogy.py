"""Genealogy: a recursive-CTE lineage walk works on hand-inserted alphas.

Proves the self-referential ``parent_id`` + ``generation`` design supports the
Evolution Engine's "walk the lineage tree" query (PLAN.md Phase 0 DoD)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Base
from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, MutationType

LINEAGE_CTE = text("""
    WITH RECURSIVE lineage(id, parent_id, generation, depth) AS (
        SELECT id, parent_id, generation, 0 FROM alphas WHERE id = :root
        UNION ALL
        SELECT a.id, a.parent_id, a.generation, l.depth + 1
        FROM alphas a JOIN lineage l ON a.parent_id = l.id
    )
    SELECT id, generation, depth FROM lineage ORDER BY depth
    """)


@pytest.fixture()
def session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'g.db'}", future=True)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s
    eng.dispose()


def _alpha(expr: str, h: str, parent=None, gen=0, mut=None) -> Alpha:
    return Alpha(
        expression=expr,
        expression_hash=h,
        status=AlphaStatus.UNTESTED.value,
        parent_id=parent,
        generation=gen,
        mutation_type=mut,
        is_valid=True,
    )


def test_recursive_lineage_walk(session: Session) -> None:
    root = _alpha("rank(ts_delta(close, 5))", "h0")
    session.add(root)
    session.flush()

    child = _alpha(
        "rank(ts_delta(close, 10))",
        "h1",
        parent=root.id,
        gen=1,
        mut=MutationType.PARAMETER_TUNE.value,
    )
    session.add(child)
    session.flush()

    grandchild = _alpha(
        "group_neutralize(rank(ts_delta(close, 10)), sector)",
        "h2",
        parent=child.id,
        gen=2,
        mut=MutationType.NEUTRALIZATION_DECAY.value,
    )
    session.add(grandchild)
    session.commit()

    rows = session.execute(LINEAGE_CTE, {"root": root.id}).all()
    # root -> child -> grandchild, depths 0,1,2
    assert [r.depth for r in rows] == [0, 1, 2]
    assert [r.generation for r in rows] == [0, 1, 2]
    assert rows[-1].id == grandchild.id
