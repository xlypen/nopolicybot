import admin_app


def _disable_auth(monkeypatch):
    monkeypatch.setattr(admin_app, "login_required", lambda f: f)


def test_api_chat_graph_contract(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/chat/all/graph?period=7")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "graph" in body


def test_api_community_health_contract(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/chat/all/community-health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "health" in body
