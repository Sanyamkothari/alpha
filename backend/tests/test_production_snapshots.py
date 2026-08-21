"""Tests for time-series alpha production performance snapshots."""

from __future__ import annotations

from app.services.alpha_library import AlphaSettings, create_alpha


def test_production_snapshots_crud(client, db_session):
    create_res = create_alpha(
        db_session,
        "rank(close)",
        AlphaSettings(region="USA", universe="TOP3000", delay=1),
    )
    alpha = create_res.alpha
    db_session.commit()

    # 1. Add Month 1 Snapshot
    res1 = client.post(
        f"/api/alphas/{alpha.id}/production-snapshots",
        json={
            "as_of_date": "2026-08-01",
            "status": "in_production",
            "pnl": 12500.50,
            "sharpe": 1.85,
            "returns": 0.042,
            "payment_amount": 350.00,
            "note": "First month in production",
        },
    )
    assert res1.status_code == 201
    d1 = res1.json()
    assert d1["pnl"] == 12500.50
    assert d1["status"] == "in_production"

    # 2. Add Month 2 Snapshot
    res2 = client.post(
        f"/api/alphas/{alpha.id}/production-snapshots",
        json={
            "as_of_date": "2026-09-01",
            "status": "in_production",
            "pnl": 24000.00,
            "sharpe": 1.92,
            "returns": 0.078,
            "payment_amount": 620.00,
            "note": "Second month performance",
        },
    )
    assert res2.status_code == 201

    # 3. List Snapshots
    list_res = client.get(f"/api/alphas/{alpha.id}/production-snapshots")
    assert list_res.status_code == 200
    snaps = list_res.json()
    assert len(snaps) == 2
    assert snaps[0]["as_of_date"] == "2026-09-01"
    assert snaps[1]["as_of_date"] == "2026-08-01"

    # 4. Upsert on same date
    res_update = client.post(
        f"/api/alphas/{alpha.id}/production-snapshots",
        json={
            "as_of_date": "2026-09-01",
            "status": "decommissioned",
            "pnl": 24000.00,
            "sharpe": 1.92,
            "returns": 0.078,
            "payment_amount": 620.00,
            "note": "Decommissioned by platform",
        },
    )
    assert res_update.status_code == 201
    assert res_update.json()["status"] == "decommissioned"

    list_res2 = client.get(f"/api/alphas/{alpha.id}/production-snapshots")
    assert len(list_res2.json()) == 2
