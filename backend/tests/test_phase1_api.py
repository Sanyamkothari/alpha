"""Phase 1 API: validate endpoint + Alpha Library + Result Importer + leaderboard.

Runs against the isolated, seeded app DB (see conftest). Expressions use distinct
windows so exact-dedup hashes don't collide across tests sharing the DB.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create(client: TestClient, expression: str, **kw) -> dict:  # type: ignore[no-untyped-def]
    body = {"expression": expression, **kw}
    r = client.post("/api/alphas", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------ validate endpoint


def test_validate_endpoint_accepts_valid(client: TestClient) -> None:
    r = client.post("/api/validate", json={"expression": "ts_mean(close, 20)"})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["normalized_expression"] == "ts_mean(close,20)"
    assert body["complexity_score"] > 0
    assert body["structural_hash"]
    assert body["features"]["time_windows"] == [20]


def test_validate_endpoint_rejects_with_codes(client: TestClient) -> None:
    r = client.post("/api/validate", json={"expression": "group_neutralize(rank(close), returns)"})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert any(e["code"] == "non_group_arg" for e in body["errors"])
    assert body["normalized_expression"] is None


# --------------------------------------------------------------- alpha library


def test_create_alpha_is_validator_gated(client: TestClient) -> None:
    created = _create(client, "ts_mean(close, 30)")
    assert created["created"] is True
    assert created["alpha"]["status"] == "untested"
    assert created["alpha"]["is_valid"] is True
    assert created["alpha"]["complexity_score"] is not None


def test_create_invalid_alpha_is_rejected_not_stored(client: TestClient) -> None:
    r = client.post("/api/alphas", json={"expression": "ts_rsi(close, 14)"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any(e["code"] == "unknown_operator" for e in detail["errors"])


def test_create_alpha_dedups_on_identical(client: TestClient) -> None:
    first = _create(client, "ts_mean(close, 40)")
    r2 = client.post("/api/alphas", json={"expression": "ts_mean(close, 40)"})
    assert r2.status_code == 200  # dedup hit is an idempotent no-op, not a 201 insert
    second = r2.json()
    assert first["created"] is True
    assert second["created"] is False
    assert second["alpha"]["id"] == first["alpha"]["id"]


def test_detail_has_creation_history(client: TestClient) -> None:
    created = _create(client, "ts_mean(close, 45)")
    alpha_id = created["alpha"]["id"]
    r = client.get(f"/api/alphas/{alpha_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["feature_json"]["structural_hash"]
    assert detail["latest_metrics"] is None
    assert detail["status_history"][0]["to_status"] == "untested"


def test_status_transition_is_logged(client: TestClient) -> None:
    alpha_id = _create(client, "ts_mean(close, 50)")["alpha"]["id"]
    r = client.post(
        f"/api/alphas/{alpha_id}/status", json={"to_status": "testing", "note": "queued"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "testing"
    detail = client.get(f"/api/alphas/{alpha_id}").json()
    assert [h["to_status"] for h in detail["status_history"]] == ["untested", "testing"]


def test_status_transition_rejects_bad_status(client: TestClient) -> None:
    alpha_id = _create(client, "ts_mean(close, 55)")["alpha"]["id"]
    r = client.post(f"/api/alphas/{alpha_id}/status", json={"to_status": "frobnicate"})
    assert r.status_code == 400


# ------------------------------------------------------------- result importer


def test_import_json_result_parses_and_transitions(client: TestClient) -> None:
    alpha_id = _create(client, "ts_mean(close, 60)")["alpha"]["id"]
    payload = {
        "sharpe": 1.85,
        "turnover": 0.12,
        "fitness": 1.10,
        "returns": 0.15,
        "longCount": 150,
        "shortCount": 148,
        "checks": [
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "HIGH_TURNOVER", "result": "PASS"},
        ],
    }
    r = client.post(f"/api/alphas/{alpha_id}/results", json={"raw": payload})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["new_status"] == "passed"  # all checks PASS
    assert out["metrics"]["sharpe"] == 1.85
    assert out["metrics"]["long_count"] == 150
    assert out["metrics"]["passed_all_checks"] is True

    detail = client.get(f"/api/alphas/{alpha_id}").json()
    assert detail["status"] == "passed"
    assert detail["latest_metrics"]["sharpe"] == 1.85


def test_import_text_block_handles_percent(client: TestClient) -> None:
    alpha_id = _create(client, "ts_mean(close, 65)")["alpha"]["id"]
    text = "Sharpe: 1.23\nTurnover: 15%\nFitness: 0.90\nReturns: 9%"
    r = client.post(f"/api/alphas/{alpha_id}/results", json={"raw": text})
    assert r.status_code == 201, r.text
    metrics = r.json()["metrics"]
    assert metrics["sharpe"] == 1.23
    assert metrics["turnover"] == 0.15  # "15%" -> 0.15
    assert abs(metrics["returns"] - 0.09) < 1e-9


def test_failing_checks_reject(client: TestClient) -> None:
    alpha_id = _create(client, "ts_mean(close, 70)")["alpha"]["id"]
    payload = {"sharpe": 0.2, "checks": [{"name": "LOW_SHARPE", "result": "FAIL"}]}
    out = client.post(f"/api/alphas/{alpha_id}/results", json={"raw": payload}).json()
    assert out["new_status"] == "rejected"


def test_latest_import_supersedes_previous(client: TestClient) -> None:
    alpha_id = _create(client, "ts_mean(close, 75)")["alpha"]["id"]
    client.post(f"/api/alphas/{alpha_id}/results", json={"raw": {"sharpe": 1.0}})
    client.post(f"/api/alphas/{alpha_id}/results", json={"raw": {"sharpe": 2.5}})
    detail = client.get(f"/api/alphas/{alpha_id}").json()
    assert detail["latest_metrics"]["sharpe"] == 2.5


# ------------------------------------------------------------------ leaderboard


def test_leaderboard_sorted_by_sharpe(client: TestClient) -> None:
    a = _create(client, "ts_mean(close, 80)")["alpha"]["id"]
    b = _create(client, "ts_mean(close, 85)")["alpha"]["id"]
    client.post(f"/api/alphas/{a}/results", json={"raw": {"sharpe": 0.9, "turnover": 0.2}})
    client.post(f"/api/alphas/{b}/results", json={"raw": {"sharpe": 2.7, "turnover": 0.1}})

    rows = client.get("/api/alphas/leaderboard?sort=sharpe&order=desc&limit=500").json()
    sharpes = [r["sharpe"] for r in rows]
    assert sharpes == sorted(sharpes, reverse=True)  # monotonic non-increasing
    ids = {r["alpha_id"] for r in rows}
    assert {a, b} <= ids


def test_leaderboard_rejects_bad_sort(client: TestClient) -> None:
    r = client.get("/api/alphas/leaderboard?sort=bogus")
    assert r.status_code == 400
