from __future__ import annotations

from services import graph_api


def test_build_graph_payload_prefers_db_when_mode_db(monkeypatch):
    monkeypatch.setattr(graph_api, "get_storage_mode", lambda: "db")

    async def _fake_db(*args, **kwargs):
        return {"nodes": [{"id": 1, "label": "A"}], "edges": [], "meta": {"source": "db"}}

    monkeypatch.setattr(graph_api, "_build_graph_payload_from_db", _fake_db)
    payload = graph_api.build_graph_payload(123, period="7d")
    assert payload["meta"]["source"] == "db"
    assert payload["nodes"]


def test_build_graph_payload_hybrid_falls_back_to_json(monkeypatch):
    monkeypatch.setattr(graph_api, "get_storage_mode", lambda: "hybrid")

    async def _fake_db(*args, **kwargs):
        return {"nodes": [], "edges": [], "meta": {"source": "db"}}

    monkeypatch.setattr(graph_api, "_build_graph_payload_from_db", _fake_db)
    monkeypatch.setattr(
        graph_api.social_graph,
        "get_connections",
        lambda _chat_id: [
            {
                "user_a": 11,
                "user_b": 22,
                "message_count_total": 3,
                "message_count_7d": 3,
                "message_count_30d": 3,
                "message_count_a_to_b": 2,
                "message_count_b_to_a": 1,
            }
        ],
    )
    monkeypatch.setattr(graph_api.user_stats, "get_user_display_names", lambda: {"11": "U11", "22": "U22"})

    payload = graph_api.build_graph_payload(123, period="7d")
    assert payload["meta"]["source"] == "json"
    assert len(payload["nodes"]) == 2
