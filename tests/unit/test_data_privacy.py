from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from db.models import Edge, Message, User, UserPortrait
from services import data_privacy


def test_user_hash_is_stable():
    h1 = data_privacy.user_hash(123, 777)
    h2 = data_privacy.user_hash(123, 777)
    h3 = data_privacy.user_hash(124, 777)
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 16


def test_prune_user_stats_messages_removes_old_rows(monkeypatch):
    payload = {
        "users": {
            "1": {
                "messages_by_chat": {"10": [{"text": "old", "date": "2020-01-01"}, {"text": "new", "date": date.today().isoformat()}]},
                "daily_buffer": [{"text": "old", "date": "2020-01-01"}],
                "messages_to_bot_buffer": [{"text": "old", "date": "2020-01-01"}],
                "images_archive": [{"description": "x", "date": "2020-01-01"}],
                "close_attention_views": [{"source": "x", "date": "2020-01-01"}],
            }
        }
    }
    saved = {}
    monkeypatch.setattr(data_privacy.user_stats, "_load", lambda: payload)
    monkeypatch.setattr(data_privacy.user_stats, "_save", lambda data: saved.setdefault("data", data))
    monkeypatch.setattr(data_privacy.user_stats, "_ensure_messages_by_chat", lambda u: False)

    res = data_privacy.prune_user_stats_messages(days=90)
    assert res["removed_messages"] >= 2
    assert res["removed_images"] == 1
    assert res["removed_messages_to_bot"] == 1
    assert "data" in saved


@pytest.mark.asyncio
async def test_erase_user_data_removes_db_and_json(db_session, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    db_session.add(User(id=42, chat_id=1, username="u42", first_name="A", last_name="B", is_active=True, joined_at=now, last_seen=now))
    db_session.add(Message(telegram_id=1001, chat_id=1, user_id=42, text="hello", media_type="text", sent_at=now))
    db_session.add(Message(telegram_id=1002, chat_id=1, user_id=7, text="reply", media_type="text", sent_at=now, replied_to=42))
    db_session.add(Edge(chat_id=1, from_user=42, to_user=7, weight=1.0, period_7d=1.0, period_30d=1.0, last_updated=now))
    db_session.add(UserPortrait(user_id=42, chat_id=1, portrait="x", generated_at=now))
    await db_session.commit()

    @asynccontextmanager
    async def _fake_get_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    monkeypatch.setattr(data_privacy, "get_db", _fake_get_db)

    user_stats_payload = {"users": {"42": {"messages_by_chat": {"1": [{"text": "x", "date": "2026-01-01"}]}}}}
    social_payload = {
        "connections": {"1": {"42|7": {"user_a": 42, "user_b": 7}}},
        "dialogue_log": {"1": {"2026-01-01": [{"sender_id": 42, "reply_to_user_id": 7, "text": "x"}]}},
        "realtime_cursors": {"1": {"42|7": 3}},
    }
    monkeypatch.setattr(data_privacy.user_stats, "_load", lambda: user_stats_payload)
    monkeypatch.setattr(data_privacy.user_stats, "_save", lambda d: user_stats_payload.update(d))
    monkeypatch.setattr(data_privacy.social_graph, "_load", lambda: social_payload)
    monkeypatch.setattr(data_privacy.social_graph, "_save", lambda d: social_payload.update(d))

    result = await data_privacy.erase_user_data(42)
    assert result["ok"] is True
    assert result["db_messages_deleted"] >= 1
    assert result["db_edges_deleted"] >= 1
    assert result["json_user_removed"] is True
    assert result["json_graph_pairs_removed"] >= 1

    users = (await db_session.execute(select(User).where(User.id == 42))).scalars().all()
    msgs = (await db_session.execute(select(Message).where(Message.user_id == 42))).scalars().all()
    edges = (await db_session.execute(select(Edge).where(Edge.from_user == 42))).scalars().all()
    assert users == []
    assert msgs == []
    assert edges == []
