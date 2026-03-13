from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app


def test_fastapi_v2_health():
    with TestClient(app) as client:
        resp = client.get("/api/v2/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


def test_fastapi_graph_unauthorized():
    with TestClient(app) as client:
        resp = client.get("/api/v2/graph/1")
        assert resp.status_code in (401, 403)


def test_fastapi_graph_delta_unauthorized():
    with TestClient(app) as client:
        resp = client.get("/api/v2/graph/1/delta")
        assert resp.status_code in (401, 403)


def test_fastapi_graph_delta_contract_authorized():
    headers = {"Authorization": "Bearer change-me-in-production"}
    with TestClient(app) as client:
        first = client.get("/api/v2/graph/1?period=7", headers=headers)
        assert first.status_code == 200
        first_body = first.json()
        assert "graph" in first_body
        version = str(first_body.get("graph_version") or "")
        assert version

        second = client.get(f"/api/v2/graph/1/delta?period=7&since={version}", headers=headers)
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["changed"] is False
        assert "delta" in second_body


def test_fastapi_metrics_contract_authorized():
    headers = {"Authorization": "Bearer change-me-in-production"}
    with TestClient(app) as client:
        resp = client.get("/api/v2/metrics", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "metrics" in body


def test_fastapi_alerts_contract_authorized():
    headers = {"Authorization": "Bearer change-me-in-production"}
    with TestClient(app) as client:
        resp = client.get("/api/v2/alerts?limit=20", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "alerts" in body
        assert "metrics" in body


def test_fastapi_rate_limit_contract(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "_hardening_config",
        lambda: {"rate_limit_per_min": 1, "max_url_length": 2400, "max_body_bytes": 1048576},
    )
    api_main._API_RATE_LIMITER.clear()
    headers = {"Authorization": "Bearer change-me-in-production"}
    with TestClient(app) as client:
        first = client.get("/api/v2/graph/1?period=7", headers=headers)
        assert first.status_code == 200
        second = client.get("/api/v2/graph/1?period=7", headers=headers)
        assert second.status_code == 429
