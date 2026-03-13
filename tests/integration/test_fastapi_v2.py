from fastapi.testclient import TestClient

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
