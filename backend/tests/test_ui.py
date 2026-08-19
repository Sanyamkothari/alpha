"""The UI's JSON contract.

These pin the shapes the single-page frontend depends on. There is no build step
and no type checking across the boundary, so a renamed field would otherwise
surface as a blank panel in the browser rather than a failing test.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.results import AlphaMetric, SimulationImport


def _seed_family(db, family: str = "ui/test@USA/TOP3000/d1") -> int:
    """One complete 2x2 corner of a surface so plateau logic has neighbours."""
    first_id = None
    for window, decay, sharpe in ((5, 0, 2.0), (10, 0, 1.9), (5, 4, 1.8), (10, 4, 1.7)):
        alpha = Alpha(
            expression=f"rank(ts_zscore(close,{window}))",
            normalized_expression=f"rank(ts_zscore(close,{window}))",
            # Family-scoped: expression_hash is globally unique, so two families
            # seeding the same window/decay must not collide.
            expression_hash=f"{family}-{window}-{decay}",
            family_key=family,
            status=AlphaStatus.REJECTED.value,
            source="constructor",
            region="USA",
            universe="TOP3000",
            delay=1,
            neutralization="SUBINDUSTRY",
            decay=decay,
            truncation=0.08,
            is_valid=True,
            generation=0,
            feature_json={
                "grid": {
                    "ts": "ts_zscore",
                    "cs": "rank",
                    "group": None,
                    "neutralization": "SUBINDUSTRY",
                    "truncation": 0.08,
                    "window": window,
                    "decay": decay,
                }
            },
        )
        db.add(alpha)
        db.flush()
        first_id = first_id or alpha.id
        imp = SimulationImport(alpha_id=alpha.id, source="brain_api", raw_payload={})
        db.add(imp)
        db.flush()
        db.add(
            AlphaMetric(
                alpha_id=alpha.id,
                simulation_import_id=imp.id,
                sharpe=sharpe,
                fitness=1.2,
                turnover=0.3,
                passed_all_checks=True,
            )
        )
    db.flush()
    db.commit()
    return first_id


def test_summary_shape(client: TestClient) -> None:
    r = client.get("/api/ui/summary")
    assert r.status_code == 200
    body = r.json()
    for key in ("counts", "shortlist", "near_misses", "jobs"):
        assert key in body, key
    for key in ("alphas", "simulated", "passing", "submitted", "families"):
        assert key in body["counts"], key


def test_surfaces_shape(client: TestClient, db_session) -> None:
    family = "ui/surface@USA/TOP3000/d1"
    _seed_family(db_session, family)

    r = client.get("/api/ui/surfaces", params={"family": family})
    assert r.status_code == 200
    body = r.json()
    assert body["family_key"] == family
    assert body["windows"] and body["decays"]
    assert body["surfaces"], "expected at least one surface"

    surface = body["surfaces"][0]
    assert set(surface["label"]) == {"ts", "cs", "group", "neutralization", "truncation"}
    # Cells are keyed "window:decay" — the frontend indexes them directly.
    cell = surface["cells"]["5:0"]
    for key in ("alpha_id", "sharpe", "fitness", "turnover", "passed", "promoted", "expression"):
        assert key in cell, key


def test_family_key_with_slashes_survives_the_round_trip(client: TestClient, db_session) -> None:
    """family_key contains '/' and '@'. As a path segment it would be mangled;
    this is why the endpoint takes it as a query parameter."""
    family = "liabilities/cap@USA/TOP3000/d1"
    _seed_family(db_session, family)
    r = client.get("/api/ui/surfaces", params={"family": family})
    assert r.status_code == 200
    assert r.json()["family_key"] == family


def test_datasets_and_suggestions_respond(client: TestClient) -> None:
    assert client.get("/api/ui/datasets").status_code == 200
    assert client.get("/api/ui/suggestions").status_code == 200


def test_mark_alpha_submitted(client: TestClient, db_session) -> None:
    """Recording a human submission must not require any platform contact."""
    alpha_id = _seed_family(db_session, "ui/mark@USA/TOP3000/d1")
    r = client.post(f"/api/ui/alphas/{alpha_id}/mark", json={"action": "submitted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == AlphaStatus.SUBMITTED.value


def test_mark_rejects_unknown_action(client: TestClient, db_session) -> None:
    alpha_id = _seed_family(db_session, "ui/mark2@USA/TOP3000/d1")
    r = client.post(f"/api/ui/alphas/{alpha_id}/mark", json={"action": "auto_submit"})
    assert r.status_code == 422


def test_launch_requires_a_field(client: TestClient) -> None:
    assert client.post("/api/ui/runs", json={}).status_code == 422


def test_unknown_job_is_404_not_a_hang(client: TestClient) -> None:
    r = client.get("/api/ui/runs/deadbeef1234")
    assert r.status_code == 404
    assert "unknown job" in r.json()["detail"]


def test_job_registry_persistence_and_recovery(tmp_path) -> None:
    import time

    from app.services.jobs import JobRegistry

    storage = tmp_path / "jobs.json"
    reg1 = JobRegistry(storage_path=storage)
    job = reg1.launch("test_kind", "Test Label", lambda job_id: {"ok": True})
    for _ in range(50):
        if reg1.get(job.id).status == "done":
            break
        time.sleep(0.02)

    # Launch another job and simulate server restart
    active_job = reg1.launch("active_kind", "Active Label", lambda job_id: time.sleep(5))
    time.sleep(0.05)

    # Re-instantiate registry from the same file (simulating server reboot)
    reg2 = JobRegistry(storage_path=storage)
    recovered_done = reg2.get(job.id)
    assert recovered_done is not None
    assert recovered_done.status == "done"

    recovered_active = reg2.get(active_job.id)
    assert recovered_active is not None
    assert recovered_active.status == "interrupted"
    assert "interrupted" in recovered_active.error


def test_index_is_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
