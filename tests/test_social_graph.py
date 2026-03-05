import social_graph


def test_pair_key_is_symmetric():
    assert social_graph._pair_key(1, 2) == social_graph._pair_key(2, 1)


def test_append_dialogue_message_skips_self_reply(tmp_path, monkeypatch):
    graph_path = tmp_path / "social_graph.json"
    monkeypatch.setattr(social_graph, "GRAPH_JSON", graph_path)
    monkeypatch.setattr(social_graph, "DATA_DIR", tmp_path)

    social_graph.append_dialogue_message(
        chat_id=1,
        sender_id=10,
        text="hello",
        reply_to_user_id=10,
        sender_name="u",
    )
    data = social_graph._load()
    assert data["dialogue_log"] == {}


def test_process_realtime_updates_builds_connection(tmp_path, monkeypatch):
    graph_path = tmp_path / "social_graph.json"
    monkeypatch.setattr(social_graph, "GRAPH_JSON", graph_path)
    monkeypatch.setattr(social_graph, "DATA_DIR", tmp_path)
    monkeypatch.setattr(social_graph, "_summarize_dialogue_pair", lambda messages, _: "короткое live-саммари")

    social_graph.append_dialogue_message(
        chat_id=1,
        sender_id=10,
        text="привет",
        reply_to_user_id=11,
        sender_name="u10",
    )
    social_graph.append_dialogue_message(
        chat_id=1,
        sender_id=11,
        text="и тебе",
        reply_to_user_id=10,
        sender_name="u11",
    )

    updated = social_graph.process_realtime_updates(min_new_messages=1)
    assert updated >= 1
    data = social_graph._load()
    chat_conn = data.get("connections", {}).get("1", {})
    assert chat_conn
    only = next(iter(chat_conn.values()))
    assert "live" in only.get("summary", "")
    assert isinstance(only.get("summary_by_date"), list)
    assert "tone" in only
    assert "topics" in only
    assert "confidence" in only


def test_get_connections_enriches_metrics_for_legacy_entry(tmp_path, monkeypatch):
    graph_path = tmp_path / "social_graph.json"
    monkeypatch.setattr(social_graph, "GRAPH_JSON", graph_path)
    monkeypatch.setattr(social_graph, "DATA_DIR", tmp_path)
    raw = {
        "dialogue_log": {},
        "processed_dates": {},
        "realtime_cursors": {},
        "last_processed_date": None,
        "connections": {
            "1": {
                "10|11": {
                    "user_a": 10,
                    "user_b": 11,
                    "summary": "[2026-03-04] обсуждали сервер и API",
                    "last_updated": "2026-03-04",
                    "message_count": 4,
                }
            }
        },
    }
    social_graph._save(raw)
    rows = social_graph.get_connections(1)
    assert len(rows) == 1
    row = rows[0]
    assert row["tone"] in {"neutral", "friendly", "conflict", "toxic"}
    assert isinstance(row["topics"], list)
    assert "message_count_7d" in row
