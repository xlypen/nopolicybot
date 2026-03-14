import os

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app

TEST_ADMIN_TOKEN = "test-admin-token-abcdefghijklmnopqrstuvwxyz"
os.environ.setdefault("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")


def test_fastapi_v2_health():
    with TestClient(app) as client:
        resp = client.get("/api/v2/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


def test_fastapi_cors_blocks_unknown_origin(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://admin.example.com")
    with TestClient(app) as client:
        resp = client.get("/api/v2/health", headers={"Origin": "https://evil.example.com"})
        assert resp.status_code == 403


def test_fastapi_cors_allows_known_origin(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://admin.example.com")
    with TestClient(app) as client:
        resp = client.get("/api/v2/health", headers={"Origin": "https://admin.example.com"})
        assert resp.status_code == 200


def test_fastapi_cors_allows_same_origin_when_allowed_origins_empty(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    with TestClient(app) as client:
        resp = client.get("/api/v2/health", headers={"Origin": "http://testserver"})
        assert resp.status_code == 200


def test_fastapi_graph_unauthorized():
    with TestClient(app) as client:
        resp = client.get("/api/v2/graph/1")
        assert resp.status_code in (401, 403)


def test_fastapi_graph_delta_unauthorized():
    with TestClient(app) as client:
        resp = client.get("/api/v2/graph/1/delta")
        assert resp.status_code in (401, 403)


def test_fastapi_graph_delta_contract_authorized():
    headers = {"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
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
    headers = {"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get("/api/v2/metrics", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "metrics" in body
        assert "realtime" in body


def test_fastapi_metrics_prom_contains_ws_queue_metric():
    headers = {"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get("/api/v2/metrics?format=prom", headers=headers)
        assert resp.status_code == 200
        text = resp.text
        assert "nopolicybot_api_v2_ws_queue_utilization" in text


def test_fastapi_alerts_contract_authorized():
    headers = {"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get("/api/v2/alerts?limit=20", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "alerts" in body
        assert "metrics" in body


def test_fastapi_realtime_ws_accepts_negative_chat_id():
    with TestClient(app) as client:
        with client.websocket_connect("/api/v2/realtime/ws/-1001758892482") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert int(connected["chat_id"]) == -1001758892482
            ws.send_text("ping")
            pong = ws.receive_json()
            assert pong["type"] == "pong"
            assert int(pong["chat_id"]) == -1001758892482


def test_fastapi_rate_limit_contract(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "_hardening_config",
        lambda: {"rate_limit_per_min": 1, "max_url_length": 2400, "max_body_bytes": 1048576},
    )
    api_main._API_RATE_LIMITER.clear()
    headers = {"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    with TestClient(app) as client:
        first = client.get("/api/v2/graph/1?period=7", headers=headers)
        assert first.status_code == 200
        second = client.get("/api/v2/graph/1?period=7", headers=headers)
        assert second.status_code == 429


def test_fastapi_user_data_delete_contract(monkeypatch):
    from services import data_privacy

    async def _fake_erase(user_id: int):
        return {"ok": True, "user_id": int(user_id), "db_messages_deleted": 2}

    monkeypatch.setattr(data_privacy, "erase_user_data", _fake_erase)
    headers = {"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    with TestClient(app) as client:
        resp = client.delete("/api/v2/users/42/data", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["result"]["user_id"] == 42
