"""Tests for submission attempts queue and derived platform outcomes."""

from __future__ import annotations

from datetime import datetime

from app.models.alphas import Alpha, SubmissionAttempt, sync_alpha_platform_outcome
from app.models.enums import AlphaStatus, PlatformOutcome, SubmissionCheckName, SubmissionResult
from app.services.alpha_library import AlphaSettings, create_alpha


def test_pending_attempt_and_unresolved_queue(client, db_session):
    create_res = create_alpha(
        db_session,
        "rank(ts_rank(close, 10))",
        AlphaSettings(region="USA", universe="TOP3000", delay=1),
    )
    alpha_id = create_res.alpha.id
    db_session.commit()

    # 1. Create a pending attempt (simulating operator pressing 'c')
    res = client.post(f"/api/alphas/{alpha_id}/attempts", json={"notes": "Copied via c"})
    assert res.status_code == 201
    att_data = res.json()
    assert att_data["result"] is None
    assert att_data["alpha_id"] == alpha_id
    assert att_data["notes"] == "Copied via c"

    # Alpha platform_outcome should be pending
    alpha_get = client.get(f"/api/alphas/{alpha_id}").json()
    assert alpha_get["platform_outcome"] == PlatformOutcome.PENDING.value

    # 2. List unresolved attempts
    unres = client.get("/api/alphas/attempts/unresolved")
    assert unres.status_code == 200
    rows = unres.json()
    assert any(r["id"] == att_data["id"] for r in rows)

    # 3. Resolve attempt as submitted
    resolve_res = client.patch(
        f"/api/alphas/attempts/{att_data['id']}/resolve",
        json={"result": "submitted", "notes": "Submitted successfully on BRAIN"},
    )
    assert resolve_res.status_code == 200
    assert resolve_res.json()["result"] == "submitted"

    # Outcome should now be derived as submitted
    alpha_get2 = client.get(f"/api/alphas/{alpha_id}").json()
    assert alpha_get2["platform_outcome"] == PlatformOutcome.SUBMITTED.value
    assert alpha_get2["status"] == AlphaStatus.SUBMITTED.value

    # Unresolved list should now be empty of this attempt
    unres2 = client.get("/api/alphas/attempts/unresolved")
    assert not any(r["id"] == att_data["id"] for r in unres2.json())


def test_rejected_attempt_and_failed_checks(client, db_session):
    create_res = create_alpha(
        db_session,
        "rank(ts_rank(volume, 10))",
        AlphaSettings(region="USA", universe="TOP3000", delay=1),
    )
    alpha_id = create_res.alpha.id
    db_session.commit()

    # Record rejected attempt with failed check
    res = client.post(
        f"/api/alphas/{alpha_id}/attempts",
        json={
            "result": "rejected",
            "failed_check": SubmissionCheckName.SELF_CORRELATION.value,
            "check_detail": "0.74 vs 0.70",
            "notes": "Failed self-correlation against #243",
        },
    )
    assert res.status_code == 201

    alpha_get = client.get(f"/api/alphas/{alpha_id}").json()
    assert alpha_get["platform_outcome"] == PlatformOutcome.REJECTED.value
    assert "SELF_CORRELATION" in (alpha_get["outcome_note"] or "")


def test_multiple_attempts_derived_outcome(client, db_session):
    create_res = create_alpha(
        db_session,
        "rank(ts_rank(vwap, 10))",
        AlphaSettings(region="USA", universe="TOP3000", delay=1),
    )
    alpha_id = create_res.alpha.id
    db_session.commit()

    # Attempt 1: rejected
    res1 = client.post(
        f"/api/alphas/{alpha_id}/attempts",
        json={"result": "rejected", "failed_check": "LOW_SHARPE", "check_detail": "1.18 vs 1.25"},
    )
    assert res1.status_code == 201
    alpha_get = client.get(f"/api/alphas/{alpha_id}").json()
    assert alpha_get["platform_outcome"] == "rejected"

    # Attempt 2: rejected again
    res2 = client.post(
        f"/api/alphas/{alpha_id}/attempts",
        json={"result": "rejected", "failed_check": "HIGH_TURNOVER", "check_detail": "0.78 vs 0.70"},
    )
    assert res2.status_code == 201
    alpha_get2 = client.get(f"/api/alphas/{alpha_id}").json()
    assert alpha_get2["platform_outcome"] == "rejected"

    # Attempt 3: submitted!
    res3 = client.post(
        f"/api/alphas/{alpha_id}/attempts",
        json={"result": "submitted", "notes": "Passed after decay tuning"},
    )
    assert res3.status_code == 201
    alpha_get3 = client.get(f"/api/alphas/{alpha_id}").json()
    assert alpha_get3["platform_outcome"] == "accepted"


def test_ui_attempt_endpoints(client, db_session):
    create_res = create_alpha(
        db_session,
        "rank(ts_rank(returns, 10))",
        AlphaSettings(region="USA", universe="TOP3000", delay=1),
    )
    alpha_id = create_res.alpha.id
    db_session.commit()

    # Create pending attempt via API
    att_res = client.post(f"/api/alphas/{alpha_id}/attempts", json={})
    assert att_res.status_code == 201
    att_id = att_res.json()["id"]

    # UI Unresolved endpoint
    ui_unres = client.get("/api/ui/attempts/unresolved")
    assert ui_unres.status_code == 200
    assert ui_unres.json()["count"] >= 1

    # UI Resolve
    res = client.post(
        f"/api/ui/attempts/{att_id}/resolve",
        json={
            "result": "rejected",
            "failed_check": "PROD_CORRELATION",
            "check_detail": "0.72 vs 0.70",
            "notes": "Prod correlation failure",
        },
    )
    assert res.status_code == 200
    assert res.json()["result"] == "rejected"

    # UI Dismiss
    att_res2 = client.post(f"/api/alphas/{alpha_id}/attempts", json={})
    assert att_res2.status_code == 201
    att_id2 = att_res2.json()["id"]
    dismiss_res = client.post(f"/api/ui/attempts/{att_id2}/dismiss")
    assert dismiss_res.status_code == 200
    assert dismiss_res.json()["dismissed"] is True
