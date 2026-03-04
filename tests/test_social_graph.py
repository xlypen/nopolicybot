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
