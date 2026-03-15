import admin_app
import re


def _disable_auth(monkeypatch):
    monkeypatch.setattr(admin_app, "login_required", lambda f: f)


def _extract_csrf_token(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html or "")
    return m.group(1) if m else ""


def test_login_rejects_wrong_password_with_message(monkeypatch):
    monkeypatch.setattr(admin_app, "_get_admin_password", lambda: "secret123")
    with admin_app.app.test_client() as client:
        page = client.get("/login")
        token = _extract_csrf_token(page.get_data(as_text=True))
        assert token
        resp = client.post("/login", data={"password": "bad-password", "csrf_token": token})
        assert resp.status_code == 401
        html = resp.get_data(as_text=True)
        assert "Неверный пароль." in html


def test_login_rejects_missing_csrf(monkeypatch):
    monkeypatch.setattr(admin_app, "_get_admin_password", lambda: "secret123")
    with admin_app.app.test_client() as client:
        resp = client.post("/login", data={"password": "secret123"})
        assert resp.status_code == 400
        html = resp.get_data(as_text=True)
        assert "Сессия формы истекла" in html


def test_admin_route_defaults_to_modern_dashboard(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/admin")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Панель мониторинга" in html or "Modern Dashboard" in html
        assert "/admin-legacy" in html


def test_admin_dashboard_has_graph_lab_tabs(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/admin")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'data-tab="graph-view"' in html
        assert 'data-tab="graph-lab"' in html
        assert "labApplyFilters" in html
        assert "/api/chat/" in html


def test_admin_legacy_route_still_available(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/admin-legacy")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Админ-панель" in html


def test_admin_legacy_query_flag_compat(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/admin?legacy=1")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Админ-панель" in html


def test_api_chat_mode_get_contract(monkeypatch):
    _disable_auth(monkeypatch)
    fake_response = {"ok": True, "mode": "soft", "descriptions": {"soft": "Реакции 1–5", "active": "Реакции с 1-го"}}

    def _fake_proxy(path, method="GET", data=None):
        return fake_response, 200

    monkeypatch.setattr(admin_app, "_proxy_to_api_v2", _fake_proxy)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/chat-mode?chat_id=123")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["mode"] == "soft"
        assert "descriptions" in body


def test_api_chat_mode_post_contract(monkeypatch):
    _disable_auth(monkeypatch)
    fake_response = {"ok": True, "mode": "active"}

    def _fake_proxy(path, method="GET", data=None):
        return fake_response, 200

    monkeypatch.setattr(admin_app, "_proxy_to_api_v2", _fake_proxy)
    with admin_app.app.test_client() as client:
        resp = client.post("/api/chat-mode", json={"chat_id": 123, "mode": "active"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["mode"] == "active"


def test_admin_user_profile_route_contract(monkeypatch):
    _disable_auth(monkeypatch)
    import user_stats

    monkeypatch.setattr(admin_app, "_load_users", lambda: {"users": {"42": {"display_name": "User42", "stats": {"total_messages": 12}}}})
    monkeypatch.setattr(admin_app, "_collect_user_connections", lambda user_id, chat_id, limit=50: ([{"peer_name": "Peer1", "message_count_7d": 3, "message_count_30d": 7, "tone": "neutral"}], [1]))
    monkeypatch.setattr(user_stats, "get_user", lambda user_id, display_name="": {"display_name": display_name or f"u{user_id}", "rank": "neutral", "stats": {"total_messages": 12, "political_messages": 2, "warnings_received": 1}})

    with admin_app.app.test_client() as client:
        resp = client.get("/admin/user/42?chat=all")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Профиль пользователя" in html
        assert "loadUserMetrics" in html
        assert "USER_ID = \"42\"" in html


def test_api_chat_graph_contract(monkeypatch):
    _disable_auth(monkeypatch)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/chat/all/graph?period=7")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "graph" in body


def test_api_v2_graph_proxy_contract(monkeypatch):
    _disable_auth(monkeypatch)
    fake_response = {"ok": True, "graph": {"nodes": [], "edges": [], "meta": {}}, "graph_version": "abc123"}

    def _fake_proxy(path, method="GET", data=None):
        return fake_response, 200

    monkeypatch.setattr(admin_app, "_proxy_to_api_v2", _fake_proxy)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/v2/graph/all?period=7d")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["graph"] == fake_response["graph"]
        assert body["graph_version"] == "abc123"


def test_api_v2_admin_proxy_contract(monkeypatch):
    _disable_auth(monkeypatch)
    fake_dashboard = {"ok": True, "dashboard": {"chat_id": "all", "health_score": 0.8}}

    def _fake_proxy(path, method="GET", data=None):
        return fake_dashboard, 200

    monkeypatch.setattr(admin_app, "_proxy_to_api_v2", _fake_proxy)
    with admin_app.app.test_client() as client:
        resp = client.get("/api/v2/admin/dashboard?chat_id=all&days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["dashboard"] == fake_dashboard["dashboard"]


def test_api_chat_graph_lab_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import graph_api

    monkeypatch.setattr(
        graph_api,
        "build_graph_payload",
        lambda chat_id, period="30d", ego_user=None, limit=None: {
            "nodes": [
                {"id": 1, "label": "u1", "community_id": 1, "influence_score": 0.8},
                {"id": 2, "label": "u2", "community_id": 1, "influence_score": 0.3},
                {"id": 3, "label": "u3", "community_id": 2, "influence_score": 0.2},
            ],
            "edges": [
                {"source": 1, "target": 2, "weight_period": 5},
                {"source": 1, "target": 3, "weight_period": 2},
            ],
            "meta": {"source": "db", "period": period},
        },
    )
    with admin_app.app.test_client() as client:
        resp = client.get(
            '/api/chat/all/graph-lab?filters={"min_degree":1,"show_centrality":true,"show_bridges":true}&period=30d'
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "graph" in body
        assert body["graph"]["meta"]["source"] == "graph-lab"
        assert len(body["graph"]["nodes"]) >= 1


def test_api_chat_conflict_prediction_contract(monkeypatch):
    _disable_auth(monkeypatch)
    import social_graph
    import user_stats

    monkeypatch.setattr(
        social_graph,
        "get_conflict_forecast",
        lambda chat_id=None, limit=120: [
            {"chat_id": 1, "user_a": 10, "user_b": 20, "tone": "toxic", "trend_delta": 0.6, "risk": 0.78, "message_count_24h": 4, "topics": ["politics"]},
            {"chat_id": 1, "user_a": 30, "user_b": 40, "tone": "neutral", "trend_delta": 0.1, "risk": 0.22, "message_count_24h": 1, "topics": []},
        ],
    )
    monkeypatch.setattr(user_stats, "get_user_display_names", lambda: {"10": "Alice", "20": "Bob", "30": "Carol", "40": "Dan"})
    with admin_app.app.test_client() as client:
        resp = client.get("/api/chat/all/conflict-prediction?threshold=0.5&days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["count"] == 1
        assert body["risks"][0]["user1"] == "Alice"


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
    fake_response = {"ok": True, "health": {"chat_id": 100, "days": 30, "health_score": 0.7}}

    def _fake_proxy(path, method="GET", data=None):
        return fake_response, 200

    monkeypatch.setattr(admin_app, "_proxy_to_api_v2", _fake_proxy)
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


def test_api_recommendations_mark_done_contract(monkeypatch):
    _disable_auth(monkeypatch)
    monkeypatch.setattr(admin_app, "write_event", lambda *args, **kwargs: None)
    with admin_app.app.test_client() as client:
        resp = client.post(
            "/api/recommendations/mark-done",
            json={
                "chat_id": "all",
                "completed": True,
                "item": {
                    "type": "retention_standard",
                    "priority": "medium",
                    "user_id": 42,
                    "reason": "churn=0.72",
                    "action": "Проверить вовлеченность",
                },
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True


def test_api_predictive_overview_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import predictive_models

    monkeypatch.setattr(
        predictive_models,
        "predict_overview",
        lambda chat_id=None, horizon_days=7, lookback_days=30: {
            "chat_id": "all" if chat_id is None else int(chat_id),
            "signals": {"churn_risk": {"predicted": 0.4}},
        },
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/predictive/overview?chat_id=all&horizon_days=7")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "overview" in body


def test_api_learning_summary_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import learning_loop

    monkeypatch.setattr(
        learning_loop,
        "feedback_summary",
        lambda chat_id=None, days=30: {"chat_id": "all", "days": int(days), "total_events": 3},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/learning/summary?chat_id=all&days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "summary" in body


def test_api_admin_dashboard_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_chat_health_dashboard",
        lambda chat_id, days=30: {"chat_id": "all", "health_score": 0.72, "messages_today": 12},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/dashboard?chat_id=all&days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "dashboard" in body


def test_api_admin_community_structure_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_community_structure_dashboard",
        lambda chat_id, period="30d", limit=1200: {"chat_id": "all", "density": 0.15, "bridge_users": []},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/community-structure?chat_id=all&period=30d")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "community" in body


def test_api_admin_leaderboard_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_user_leaderboard_dashboard",
        lambda chat_id, metric="engagement", limit=10, days=30: {"metric": metric, "users": [{"rank": 1, "user_id": 1}]},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/leaderboard?chat_id=all&metric=engagement&limit=5")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "leaderboard" in body


def test_api_admin_at_risk_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_at_risk_users_dashboard",
        lambda chat_id, threshold=0.6, days=30, limit=30: {"count": 1, "users": [{"user_id": 1, "churn_risk": 0.8}]},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/at-risk-users?chat_id=all&threshold=0.6")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "at_risk" in body


def test_api_admin_at_risk_action_contract(monkeypatch):
    _disable_auth(monkeypatch)
    monkeypatch.setattr(admin_app, "write_event", lambda *args, **kwargs: None)
    with admin_app.app.test_client() as client:
        resp = client.post("/api/admin/at-risk-action", json={"action": "dm", "chat_id": "all", "user_id": 42})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["action"] == "dm"
        assert body["queued"] is True


def test_api_admin_decision_quality_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_decision_quality_dashboard",
        lambda chat_id, period_days=7: {"total_decisions": 10, "approval_rate": 0.8},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/decision-quality?chat_id=all&period_days=7")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "quality" in body


def test_api_admin_content_analysis_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_content_analysis_dashboard",
        lambda chat_id, period_days=30: {"top_topics": [], "sentiment": {"positive": 0.5}},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/content-analysis?chat_id=all&period_days=30")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "analysis" in body


def test_api_admin_moderation_activity_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_moderation_activity_dashboard",
        lambda chat_id, period_days=7: {"total_messages": 100, "ai_decisions": 20},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/moderation-activity?chat_id=all&period_days=7")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "activity" in body


def test_api_admin_trends_contract(monkeypatch):
    _disable_auth(monkeypatch)
    from services import admin_dashboards as adm

    monkeypatch.setattr(
        adm,
        "build_growth_trends_dashboard",
        lambda chat_id, lookback_days=30, horizon_days=7: {"user_growth": {"net_growth": 3}, "forecast": {}},
    )
    with admin_app.app.test_client() as client:
        resp = client.get("/api/admin/trends?chat_id=all&lookback_days=30&horizon_days=7")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "trends" in body


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


def test_flask_cors_blocks_unknown_origin(monkeypatch):
    _disable_auth(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://admin.example.com")
    with admin_app.app.test_client() as client:
        resp = client.get("/api/storage/status", headers={"Origin": "https://evil.example.com"})
        assert resp.status_code == 403


def test_flask_cors_allows_known_origin(monkeypatch):
    _disable_auth(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://admin.example.com")
    with admin_app.app.test_client() as client:
        resp = client.get("/api/storage/status", headers={"Origin": "https://admin.example.com"})
        assert resp.status_code == 200


def test_flask_cors_allows_same_origin_when_allowed_origins_empty(monkeypatch):
    _disable_auth(monkeypatch)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    with admin_app.app.test_client() as client:
        resp = client.post(
            "/api/portrait-from-storage",
            headers={"Origin": "http://localhost"},
            json={"user_id": 123, "chat_id": "all"},
        )
        assert resp.status_code != 403
        body = resp.get_json() or {}
        assert body.get("error") != "origin not allowed"


def test_api_portrait_classify_unknown_contract(monkeypatch):
    _disable_auth(monkeypatch)
    fake_response = {
        "ok": True,
        "chat_id": 123,
        "unknown_total": 1,
        "processed": 1,
        "skipped_no_messages": 0,
        "skipped_in_progress": 0,
        "failed": 0,
    }

    def _fake_proxy(path, method="GET", data=None):
        return fake_response, 200

    monkeypatch.setattr(admin_app, "_proxy_to_api_v2", _fake_proxy)
    with admin_app.app.test_client() as client:
        resp = client.post("/api/portrait-classify-unknown", json={"chat_id": 123})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["unknown_total"] == 1
        assert body["processed"] == 1
        assert body["failed"] == 0


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
