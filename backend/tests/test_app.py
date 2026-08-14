"""App boots and the Phase 0 read endpoints serve the seeded data.

These run against an isolated, freshly-seeded SQLite DB (see ``conftest.py``),
so they assert the wiring AND the seed contract without depending on the dev DB.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db_ok"] is True
    # Assert the *rule*, not a fixed value: status must track its inputs. Pinning
    # "ok" made this test depend on whether the developer happened to have an LLM
    # API key in .env — a missing optional key would fail the suite for no reason.
    assert body["status"] == ("ok" if body["llm_ok"] else "degraded")
    # The contract that actually holds now that simulations are automated.
    assert body["auto_simulate"] is True
    assert body["auto_submit"] is False


def test_safety_banner_present(client: TestClient) -> None:
    r = client.get("/api/system/banner")
    assert r.status_code == 200
    body = r.json()
    # The invariant that actually holds: simulation may be automated, submission
    # never is. Assert the flag, not prose that is free to be reworded.
    assert body["auto_submit"] is False
    assert "submit" in body["safety_banner"].lower()


def test_modules_listed(client: TestClient) -> None:
    r = client.get("/api/system/modules")
    assert r.status_code == 200
    assert len(r.json()["modules"]) >= 9


def test_operators_list_seeded(client: TestClient) -> None:
    r = client.get("/api/operators")
    assert r.status_code == 200
    names = {o["name"] for o in r.json()}
    # The seeded corpus is present unconditionally (isolated DB).
    assert "ts_rank" in names
    assert "group_neutralize" in names


def test_fields_list_seeded(client: TestClient) -> None:
    r = client.get("/api/fields?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "total" in body and "items" in body
    assert body["total"] >= 1
