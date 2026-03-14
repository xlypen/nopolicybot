from __future__ import annotations

import pytest

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


def test_build_graph_payload_db_first_falls_back_to_json(monkeypatch):
    monkeypatch.setattr(graph_api, "get_storage_mode", lambda: "db_first")

    async def _fake_db(*args, **kwargs):
        return {"nodes": [], "edges": [], "meta": {"source": "db"}}

    monkeypatch.setattr(graph_api, "_build_graph_payload_from_db", _fake_db)
    monkeypatch.setattr(
        graph_api.social_graph,
        "get_connections",
        lambda _chat_id: [
            {
                "user_a": 1,
                "user_b": 2,
                "message_count_total": 2,
                "message_count_7d": 2,
                "message_count_30d": 2,
                "message_count_a_to_b": 1,
                "message_count_b_to_a": 1,
            }
        ],
    )
    monkeypatch.setattr(graph_api.user_stats, "get_user_display_names", lambda: {"1": "U1", "2": "U2"})

    payload = graph_api.build_graph_payload(123, period="7d")
    assert payload["meta"]["source"] == "json"
    assert len(payload["nodes"]) == 2


def test_build_graph_payload_db_only_does_not_fallback(monkeypatch):
    monkeypatch.setattr(graph_api, "get_storage_mode", lambda: "db_only")

    async def _fake_db(*args, **kwargs):
        return {"nodes": [], "edges": [], "meta": {"source": "db"}}

    monkeypatch.setattr(graph_api, "_build_graph_payload_from_db", _fake_db)
    monkeypatch.setattr(graph_api.social_graph, "get_connections", lambda _chat_id: [])

    payload = graph_api.build_graph_payload(123, period="7d")
    assert payload["meta"]["source"] in {"db", "db_empty"}
    assert payload["nodes"] == []


def test_build_graph_payload_all_chats_prefers_db_in_db_only(monkeypatch):
    monkeypatch.setattr(graph_api, "get_storage_mode", lambda: "db_only")

    async def _fake_db(*args, **kwargs):
        return {"nodes": [{"id": 1, "label": "U1"}], "edges": [], "meta": {"source": "db"}}

    monkeypatch.setattr(graph_api, "_build_graph_payload_from_db", _fake_db)
    monkeypatch.setattr(graph_api.social_graph, "get_connections", lambda _chat_id: [])

    payload = graph_api.build_graph_payload(None, period="7d")
    assert payload["meta"]["source"] == "db"
    assert payload["nodes"]


@pytest.mark.asyncio
async def test_build_graph_payload_works_with_running_event_loop(monkeypatch):
    monkeypatch.setattr(graph_api, "get_storage_mode", lambda: "dual")

    async def _fake_db(*args, **kwargs):
        return {"nodes": [{"id": 10, "label": "U10"}], "edges": [], "meta": {"source": "db"}}

    monkeypatch.setattr(graph_api, "_build_graph_payload_from_db", _fake_db)
    monkeypatch.setattr(
        graph_api.social_graph,
        "get_connections",
        lambda _chat_id: [
            {
                "user_a": 10,
                "user_b": 20,
                "message_count_total": 4,
                "message_count_7d": 4,
                "message_count_30d": 4,
                "message_count_a_to_b": 2,
                "message_count_b_to_a": 2,
            }
        ],
    )
    monkeypatch.setattr(graph_api.user_stats, "get_user_display_names", lambda: {"10": "U10", "20": "U20"})

    # Call from inside asyncio loop: DB path should fail gracefully and fallback to JSON.
    payload = graph_api.build_graph_payload(123, "7d")
    assert payload["meta"]["source"] == "json"
    assert payload["nodes"]
