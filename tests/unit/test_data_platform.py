from __future__ import annotations

from services import data_platform


def test_export_snapshot_shape(monkeypatch):
    monkeypatch.setattr(data_platform, "_json_counts", lambda: {"users": 2, "messages": 5, "edges": 3, "chats": 1})
    monkeypatch.setattr(data_platform, "_safe_db_counts", lambda: ({"users": 2, "messages": 4, "edges": 3, "chats": 1}, None))
    payload = data_platform.export_snapshot()
    assert payload["ok"] is True
    assert payload["json"]["users"] == 2
    assert payload["db"]["messages"] == 4
    assert "delta_db_minus_json" in payload
