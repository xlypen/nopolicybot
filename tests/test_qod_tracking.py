import json
from pathlib import Path

import qod_tracking


def test_qod_tracking_add_and_load(tmp_path, monkeypatch):
    track_path = tmp_path / "qod.json"
    monkeypatch.setattr(qod_tracking, "TRACKING_PATH", track_path)

    qod_tracking.add(chat_id=1, message_id=10, user_id=42, question="q")
    data = qod_tracking.load()
    assert "by_reply" in data
    assert "1_10" in data["by_reply"]
    assert data["by_reply"]["1_10"]["user_id"] == 42

    raw = json.loads(Path(track_path).read_text(encoding="utf-8"))
    assert "by_user_private" in raw
