from __future__ import annotations

from services import learning_loop


def test_learning_loop_variant_is_deterministic():
    a = learning_loop.select_variant(1001, 2002)
    b = learning_loop.select_variant(1001, 2002)
    assert a["variant"] == b["variant"]
    assert isinstance(a["bias"], dict)


def test_learning_loop_feedback_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(learning_loop, "_PATH", tmp_path / "learning_feedback.json")
    learning_loop.append_feedback(
        event_id="evt-a",
        chat_id=1,
        user_id=11,
        strategy="motivating",
        variant="retention_bias",
        score=1.0,
        label="approve",
    )
    learning_loop.append_feedback(
        event_id="evt-b",
        chat_id=1,
        user_id=12,
        strategy="motivating",
        variant="retention_bias",
        score=-1.0,
        label="reject",
    )
    summary = learning_loop.feedback_summary(chat_id=1, days=30)
    assert summary["total_events"] == 2
    assert summary["strategy"]
    assert summary["variant"]
