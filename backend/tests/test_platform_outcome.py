from sqlalchemy import select

from app.models.alphas import (
    Alpha,
    AlphaStatusHistory,
    SubmissionAttempt,
    sync_alpha_platform_outcome,
)
from app.models.enums import AlphaStatus
from app.services.alpha_library import AlphaSettings, create_alpha


def test_set_platform_outcome_via_attempt(client, db_session):
    res = create_alpha(db_session, "rank(close)", AlphaSettings())
    alpha_id = res.alpha.id
    db_session.commit()

    # Record submitted attempt
    resp = client.post(
        f"/api/alphas/{alpha_id}/attempts",
        json={
            "result": "submitted",
            "notes": "Approved by BRAIN reviewer",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["alpha_id"] == alpha_id
    assert data["result"] == "submitted"
    assert data["notes"] == "Approved by BRAIN reviewer"

    # Verify alpha row derived outcome
    db_session.expire_all()
    alpha = db_session.get(Alpha, alpha_id)
    assert alpha.platform_outcome == "submitted"
    assert alpha.outcome_note == "Approved by BRAIN reviewer"
    assert alpha.status == "submitted"

    # Verify audit history
    hist = db_session.execute(
        select(AlphaStatusHistory)
        .where(AlphaStatusHistory.alpha_id == alpha_id)
        .order_by(AlphaStatusHistory.id.desc())
    ).scalars().first()
    assert "attempt:submitted" in hist.note


def test_direct_outcome_post_dropped(client, db_session):
    res = create_alpha(db_session, "rank(volume)", AlphaSettings())
    alpha_id = res.alpha.id
    db_session.commit()

    # Direct setter POST was dropped per user instruction
    resp = client.post(
        f"/api/alphas/{alpha_id}/outcome",
        json={"platform_outcome": "accepted"},
    )
    assert resp.status_code in (404, 405)


def test_ui_portfolio_returns_outcome_fields(client, db_session):
    res = create_alpha(db_session, "rank(ts_delta(close, 5))", AlphaSettings())
    alpha = res.alpha
    alpha.status = AlphaStatus.SUBMITTED.value
    db_session.commit()

    att = SubmissionAttempt(
        alpha_id=alpha.id,
        result="rejected",
        failed_check="SELF_CORRELATION",
        notes="High self-correlation",
    )
    db_session.add(att)
    db_session.commit()
    sync_alpha_platform_outcome(db_session, alpha.id)
    db_session.commit()

    resp = client.get("/api/ui/portfolio")
    assert resp.status_code == 200
    items = resp.json().get("portfolio", [])
    matched = [x for x in items if x["alpha_id"] == alpha.id]
    assert len(matched) == 1
    assert matched[0]["platform_outcome"] == "rejected"
