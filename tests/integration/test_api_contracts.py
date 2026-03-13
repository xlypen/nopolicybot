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


def test_api_chat_graph_delta_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import graph_api

    monkeypatch.setattr(
        graph_api,
        "build_graph_payload",
        lambda chat_id, period="7d", ego_user=None, limit=None: {
            "nodes": [{"id": 1, "label": "U1", "influence_score": 0.1, "centrality": 0.2, "community_id": 0, "tier": "secondary"}],
            "edges": [{"source": 1, "target": 2, "weight_period": 1.0, "bridge_score": 0.0, "community_id": -1}],
            "meta": {"source": "db", "period": "7d"},
        },
    )
    with admin_app.app.test_client() as client:
        first = client.get("/api/chat/all/graph-delta?period=7d").get_json()
        assert first["ok"] is True
        assert "delta" in first
        second = client.get(f"/api/chat/all/graph-delta?period=7d&since={first['graph_version']}").get_json()
        assert second["ok"] is True
        assert second["changed"] is False


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


def test_api_decisions_feedback_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import decision_engine

    monkeypatch.setattr(
        decision_engine,
        "apply_decision_feedback",
        lambda event_id, feedback, score=None, reviewer="admin", note="": {
            "event_id": event_id,
            "feedback_label": feedback,
            "feedback_score": 1.0 if feedback == "approve" else 0.0,
        },
    )
    with admin_app.app.test_client() as client:
        resp = client.post(
            "/api/decisions/feedback",
            json={"event_id": "evt-1", "feedback": "approve", "reviewer": "qa"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "decision" in body


def test_api_decisions_quality_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import decision_engine

    monkeypatch.setattr(
        decision_engine,
        "get_decision_quality",
        lambda chat_id=None, days=30: {
            "chat_id": "all",
            "days": days,
            "total_decisions": 10,
            "feedback_count": 4,
            "approval_rate": 0.75,
        },
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/decisions/quality?days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "quality" in body


def test_api_recommendations_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import recommendations

    monkeypatch.setattr(
        recommendations,
        "build_recommendations",
        lambda chat_id=None, days=30, limit=20: {"chat_id": "all", "items": [{"type": "retention_standard"}]},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/recommendations?chat_id=all&days=30&limit=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "recommendations" in body


def test_api_retention_dashboard_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import recommendations

    monkeypatch.setattr(
        recommendations,
        "build_retention_dashboard",
        lambda chat_id=None, days=30, limit=50: {"summary": {"users_total": 2}, "at_risk": []},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/retention-dashboard?chat_id=all&days=30&limit=50")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "dashboard" in body


def test_api_churn_snapshots_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import recommendations

    monkeypatch.setattr(
        recommendations,
        "get_recent_churn_snapshots",
        lambda limit=10, chat_id=None: [{"chat_id": "all", "summary": {"at_risk_count": 1}}],
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/churn/snapshots?limit=5")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert isinstance(body["snapshots"], list)


def test_api_churn_run_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import recommendations

    monkeypatch.setattr(
        recommendations,
        "run_churn_detection",
        lambda chat_id=None, days=30, limit=300: {"chat_id": "all", "summary": {"users_considered": 3}},
    )
    with admin_app.app.test_client() as client:
        resp = client.post("/api/churn/run", json={"chat_id": "all", "days": 30})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "snapshot" in body


def test_admin_recommendations_alias_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import recommendations

    monkeypatch.setattr(
        recommendations,
        "build_recommendations",
        lambda chat_id=None, days=30, limit=20: {"chat_id": "all", "items": []},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/admin/recommendations")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True


def test_api_storage_status_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import data_platform

    monkeypatch.setattr(
        data_platform,
        "export_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "storage_primary": "hybrid",
            "json": {"users": 1, "messages": 2, "edges": 3, "chats": 1},
            "db": {"users": 1, "messages": 2, "edges": 3, "chats": 1},
        },
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/storage/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "json" in body and "db" in body


def test_api_storage_cutover_report_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import storage_cutover

    monkeypatch.setattr(
        storage_cutover,
        "build_cutover_report",
        lambda: {"ok": True, "current_mode": "hybrid", "db_ready_for_cutover": True},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/storage/cutover-report")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["current_mode"] == "hybrid"


def test_api_storage_cutover_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import storage_cutover

    monkeypatch.setattr(
        storage_cutover,
        "apply_cutover",
        lambda mode, force=False, reason="manual": {"ok": True, "mode": mode, "report": {"ok": True}},
    )
    with admin_app.app.test_client() as client:
        resp = client.post("/api/storage/cutover", json={"mode": "db", "force": False, "reason": "test"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["mode"] == "db"


def test_api_topic_policies_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import topic_policies as tp

    monkeypatch.setattr(tp, "get_primary_topic", lambda chat_id=None: "politics")
    monkeypatch.setattr(tp, "get_topic_policies", lambda: {"politics": {"enabled": True, "action": "moderate"}})
    with admin_app.app.test_client() as client:
        resp = client.get("/api/topic-policies")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["primary_topic"] == "politics"
        assert "policies" in body


def test_api_topic_policies_update_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import topic_policies as tp

    monkeypatch.setattr(tp, "set_primary_topic", lambda topic: topic)
    monkeypatch.setattr(tp, "set_topic_policy", lambda name, patch: {"politics": {"enabled": True}})
    monkeypatch.setattr(tp, "get_primary_topic", lambda chat_id=None: "politics")
    monkeypatch.setattr(tp, "get_topic_policies", lambda: {"politics": {"enabled": True, "action": "moderate"}})
    with admin_app.app.test_client() as client:
        resp = client.post(
            "/api/topic-policies",
            json={"primary_topic": "politics", "name": "politics", "patch": {"enabled": True}},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "policies" in body


def test_api_me_graph_version_contract(monkeypatch):
    monkeypatch.setattr(admin_app, "_participant_verify", lambda token: (123, None))
    import social_graph

    monkeypatch.setattr(social_graph, "get_graph_version", lambda: "v-test")
    with admin_app.app.test_client() as client:
        resp = client.get("/api/me/graph-version?token=test-token")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["version"] == "v-test|u123"


def test_api_me_graph_delta_contract(monkeypatch):
    monkeypatch.setattr(admin_app, "_participant_verify", lambda token: (123, None))
    from services import graph_api

    monkeypatch.setattr(
        graph_api,
        "build_graph_payload",
        lambda chat_id, period="7d", ego_user=None, limit=None: {
            "nodes": [{"id": 123, "label": "U123", "influence_score": 0.1, "centrality": 0.2, "community_id": 0, "tier": "secondary"}],
            "edges": [],
            "meta": {"source": "db", "period": "7d"},
        },
    )
    with admin_app.app.test_client() as client:
        first = client.get("/api/me/graph?token=t").get_json()
        assert first["ok"] is True
        assert "graph_version" in first
        second = client.get(f"/api/me/graph-delta?token=t&since={first['graph_version']}").get_json()
        assert second["ok"] is True
        assert second["changed"] is False
        assert "delta" in second


def test_api_monitoring_metrics_contract(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/monitoring/metrics")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "metrics" in body


def test_api_monitoring_alerts_contract(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/monitoring/alerts?limit=20")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "alerts" in body
