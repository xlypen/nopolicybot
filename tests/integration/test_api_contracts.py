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


def test_api_metrics_user_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import marketing_metrics as mm

    monkeypatch.setattr(
        mm,
        "get_user_metrics",
        lambda user_id, chat_id=None, days=30: {
            "user_id": int(user_id),
            "chat_id": "all" if chat_id is None else int(chat_id),
            "days": int(days),
            "engagement_score": 0.5,
        },
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/metrics/user/42?chat_id=all&days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "metrics" in body


def test_api_metrics_chat_health_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import marketing_metrics as mm

    monkeypatch.setattr(
        mm,
        "get_chat_health",
        lambda chat_id, days=30: {
            "chat_id": int(chat_id),
            "days": int(days),
            "health_score": 0.7,
        },
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/metrics/chat/100/health?days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "health" in body


def test_api_leaderboard_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import marketing_metrics as mm

    monkeypatch.setattr(
        mm,
        "get_leaderboard",
        lambda metric="engagement", chat_id=None, days=30, limit=10: [
            {"user_id": 1, "score": 0.9},
            {"user_id": 2, "score": 0.7},
        ],
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/leaderboard?metric=engagement&chat_id=all&days=30&limit=5")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["metric"] == "engagement"
        assert isinstance(body["rows"], list)


def test_api_decisions_recent_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import decision_engine

    monkeypatch.setattr(
        decision_engine,
        "get_recent_decisions",
        lambda limit=80, chat_id=None, user_id=None: [
            {"strategy": "motivating", "chat_id": 1, "user_id": 2},
            {"strategy": "strict", "chat_id": 1, "user_id": 3},
        ],
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/decisions/recent?limit=10&chat_id=1")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert isinstance(body["decisions"], list)
