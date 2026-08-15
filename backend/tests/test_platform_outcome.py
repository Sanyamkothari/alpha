from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.alphas import Alpha, AlphaStatusHistory
from app.models.enums import AlphaStatus, OutcomeSource, PlatformOutcome
from app.services.alpha_library import AlphaSettings, create_alpha


def test_set_platform_outcome_api(client, db_session):
    res = create_alpha(db_session, "rank(close)", AlphaSettings())
    alpha_id = res.alpha.id
    db_session.commit()

    resp = client.post(
        f"/api/alphas/{alpha_id}/outcome",
        json={
            "platform_outcome": "accepted",
            "outcome_date": "2026-08-15",
            "outcome_note": "Approved by BRAIN reviewer",
            "outcome_source": "manual",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["alpha_id"] == alpha_id
    assert data["platform_outcome"] == "accepted"
    assert data["outcome_date"] == "2026-08-15"
    assert data["outcome_note"] == "Approved by BRAIN reviewer"
    assert data["outcome_source"] == "manual"

    # Verify alpha row
    db_session.expire_all()
    alpha = db_session.get(Alpha, alpha_id)
    assert alpha.platform_outcome == "accepted"
    assert str(alpha.outcome_date) == "2026-08-15"
    assert alpha.outcome_note == "Approved by BRAIN reviewer"
    assert alpha.outcome_source == "manual"

    # Verify audit history
    hist = db_session.execute(
        select(AlphaStatusHistory)
        .where(AlphaStatusHistory.alpha_id == alpha_id)
        .order_by(AlphaStatusHistory.id.desc())
    ).scalars().first()
    assert "outcome:accepted: Approved by BRAIN reviewer" in hist.note


def test_set_platform_outcome_invalid_value(client, db_session):
    res = create_alpha(db_session, "rank(volume)", AlphaSettings())
    alpha_id = res.alpha.id
    db_session.commit()

    resp = client.post(
        f"/api/alphas/{alpha_id}/outcome",
        json={"platform_outcome": "invalid_status"},
    )
    assert resp.status_code == 422


def test_ui_portfolio_returns_outcome_fields(client, db_session):
    res = create_alpha(db_session, "rank(close)", AlphaSettings())
    alpha = res.alpha
    alpha.status = AlphaStatus.SUBMITTED.value
    alpha.platform_outcome = PlatformOutcome.REJECTED.value
    alpha.outcome_date = date(2026, 8, 14)
    alpha.outcome_note = "High self-correlation"
    alpha.outcome_source = OutcomeSource.MANUAL.value
    db_session.commit()

    resp = client.get("/api/ui/portfolio")
    assert resp.status_code == 200
    items = resp.json().get("portfolio", [])
    matched = [x for x in items if x["alpha_id"] == alpha.id]
    assert len(matched) == 1
    assert matched[0]["platform_outcome"] == "rejected"
    assert matched[0]["outcome_date"] == "2026-08-14"
    assert matched[0]["outcome_note"] == "High self-correlation"
