from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from db.models import Edge, Message, User
from services import db_ingest


@pytest.mark.asyncio
async def test_ingest_message_event_writes_db(db_session, monkeypatch):
    monkeypatch.setattr(db_ingest, "get_storage_mode", lambda: "db")

    @asynccontextmanager
    async def _fake_get_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    monkeypatch.setattr(db_ingest, "get_db", _fake_get_db)

    ok = await db_ingest.ingest_message_event(
        chat_id=1001,
        user_id=77,
        message_id=11,
        text="hello world",
        username="user77",
        first_name="U",
        replied_to_user_id=88,
        media_type="text",
        sentiment="neutral",
        is_political=True,
    )
    assert ok is True

    users = (await db_session.execute(select(User))).scalars().all()
    msgs = (await db_session.execute(select(Message))).scalars().all()
    edges = (await db_session.execute(select(Edge))).scalars().all()

    assert len(users) == 1
    assert len(msgs) == 1
    assert len(edges) == 1
    assert msgs[0].chat_id == 1001
    assert msgs[0].user_id == 77
    assert (msgs[0].risk_flags or [])[0] == "politics"


@pytest.mark.asyncio
async def test_ingest_message_event_skips_in_json_mode(monkeypatch):
    monkeypatch.setattr(db_ingest, "get_storage_mode", lambda: "json")
    ok = await db_ingest.ingest_message_event(
        chat_id=1,
        user_id=1,
        message_id=1,
        text="ignored",
    )
    assert ok is False
