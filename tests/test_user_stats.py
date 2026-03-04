import user_stats


def test_record_chat_message_increments_total(tmp_path, monkeypatch):
    users_path = tmp_path / "user_stats.json"
    monkeypatch.setattr(user_stats, "USERS_JSON", users_path)
    monkeypatch.setattr(user_stats, "DATA_DIR", tmp_path)

    user_stats.record_chat_message(1, "hello", "name", chat_id=-100, chat_title="chat")
    u = user_stats.get_user(1)
    assert u["stats"]["total_messages"] == 1
    assert "-100" in u["messages_by_chat"]
