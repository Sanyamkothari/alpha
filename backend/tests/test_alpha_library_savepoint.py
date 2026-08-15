"""Regression test for F2: savepoint isolation in create_alpha during collision."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.alphas import Alpha
from app.services.alpha_library import AlphaSettings, create_alpha


def test_create_alpha_collision_preserves_batch(db_session) -> None:
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)

    # 1. Create Alpha A
    res_a = create_alpha(db_session, "rank(close)", settings)
    assert res_a.created is True

    # 2. Create Alpha B
    res_b = create_alpha(db_session, "rank(returns)", settings)
    assert res_b.created is True

    # 3. Create Alpha A again (collides mid-session)
    res_dup = create_alpha(db_session, "rank(close)", settings)
    assert res_dup.created is False
    assert res_dup.alpha.id == res_a.alpha.id

    # 4. Create Alpha C
    res_c = create_alpha(db_session, "rank(volume)", settings)
    assert res_c.created is True

    # 5. Commit session and verify all 3 distinct alphas survived
    db_session.commit()

    all_alphas = db_session.execute(select(Alpha).order_by(Alpha.id)).scalars().all()
    assert len(all_alphas) == 3
    exprs = [a.expression for a in all_alphas]
    assert exprs == ["rank(close)", "rank(returns)", "rank(volume)"]
