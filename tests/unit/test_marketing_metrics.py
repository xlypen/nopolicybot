from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import marketing_metrics as mm


@pytest.fixture
def isolated_metrics_store(tmp_path, monkeypatch):
    data_file = tmp_path / "marketing_metrics.json"
    monkeypatch.setattr(mm, "_DATA_PATH", data_file)
    monkeypatch.setattr(
        mm,
        "_graph_lookup",
        lambda _chat_id: {
            101: {"pagerank": 0.75, "reach": 0.7},
            202: {"pagerank": 0.2, "reach": 0.2},
            303: {"pagerank": 0.45, "reach": 0.4},
        },
    )
    return data_file


def _ts(day: str, hour: int = 12) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00").astimezone(timezone.utc)


def test_user_metrics_are_computed(isolated_metrics_store):
    mm.record_message_event(chat_id=5001, user_id=101, display_name="Alice", timestamp=_ts("2026-03-10", 10))
    mm.record_message_event(chat_id=5001, user_id=202, display_name="Bob", timestamp=_ts("2026-03-10", 11))
    mm.record_message_event(
        chat_id=5001,
        user_id=101,
        reply_to_user_id=202,
        mentioned_user_ids=[202],
        timestamp=_ts("2026-03-10", 12),
    )
    mm.record_signal_event(chat_id=5001, user_id=101, sentiment="positive", is_political=True, timestamp=_ts("2026-03-10", 12))

    payload = mm.get_user_metrics(101, chat_id=5001, days=30)
    assert payload["display_name"] == "Alice"
    assert payload["totals"]["messages"] >= 2
    assert payload["totals"]["replies_sent"] >= 1
    assert payload["totals"]["mentions_sent"] >= 1
    assert payload["engagement_score"] > 0
    assert payload["influence_score"] > 0
    assert 0 <= payload["retention_score"] <= 1


def test_chat_health_summary(isolated_metrics_store):
    mm.record_message_event(chat_id=7001, user_id=101, display_name="Alice", timestamp=_ts("2026-03-10", 10))
    mm.record_message_event(chat_id=7001, user_id=202, display_name="Bob", timestamp=_ts("2026-03-10", 11))
    mm.record_signal_event(chat_id=7001, user_id=202, sentiment="negative", is_political=False, timestamp=_ts("2026-03-10", 11))
    mm.record_message_event(chat_id=7001, user_id=303, display_name="Carol", timestamp=_ts("2026-03-11", 9))

    health = mm.get_chat_health(7001, days=30)
    assert health["participants"] == 3
    assert health["messages"] >= 3
    assert 0 <= health["health_score"] <= 1
    assert health["health_status"] in {"healthy", "needs_attention", "critical"}


def test_leaderboard_sorted_by_engagement(isolated_metrics_store):
    mm.record_message_event(chat_id=9001, user_id=101, display_name="Alice", timestamp=_ts("2026-03-10", 10))
    mm.record_message_event(chat_id=9001, user_id=202, display_name="Bob", timestamp=_ts("2026-03-10", 11))
    mm.record_message_event(chat_id=9001, user_id=101, reply_to_user_id=202, timestamp=_ts("2026-03-10", 12))
    mm.record_message_event(chat_id=9001, user_id=101, reply_to_user_id=202, timestamp=_ts("2026-03-10", 13))

    rows = mm.get_leaderboard(metric="engagement", chat_id=9001, days=30, limit=5)
    assert rows
    assert rows[0]["user_id"] == 101
