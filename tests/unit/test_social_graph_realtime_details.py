from __future__ import annotations

import copy
from datetime import date as dt_date

import social_graph


def test_process_realtime_updates_returns_details(monkeypatch):
    today = "2026-03-13"
    initial = {
        "dialogue_log": {
            "-100123": {
                today: [
                    {"sender_id": 10, "reply_to_user_id": 20, "text": "a"},
                    {"sender_id": 20, "reply_to_user_id": 10, "text": "b"},
                ]
            }
        },
        "processed_dates": {},
        "connections": {},
        "realtime_cursors": {},
        social_graph.LAST_PROCESSED_KEY: None,
    }

    saved = {}

    class _FakeDate:
        @staticmethod
        def today():
            return dt_date.fromisoformat(today)

    monkeypatch.setattr(social_graph, "date", _FakeDate)
    monkeypatch.setattr(social_graph, "_load", lambda: copy.deepcopy(initial))
    monkeypatch.setattr(social_graph, "_save", lambda data: saved.update(copy.deepcopy(data)))
    monkeypatch.setattr(social_graph, "_summarize_dialogue_pair", lambda pair_msgs, names: "summary")

    out = social_graph.process_realtime_updates(min_new_messages=1, return_details=True)
    assert out["updated"] == 1
    assert out["by_chat"] == {-100123: 1}
    assert "summary" in saved["connections"]["-100123"]["10|20"]["summary"]
