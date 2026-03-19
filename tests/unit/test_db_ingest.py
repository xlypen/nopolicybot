from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from db.models import Edge, Message, User
from services import db_ingest


def test_combined_telegram_id_fits_signed_64bit():
    value = db_ingest._combined_telegram_id(-1001758892482, 216)
    assert 0 <= value <= (1 << 63) - 1


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


@pytest.mark.asyncio
async def test_ingest_message_event_same_user_different_chats(db_session, monkeypatch):
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

    ok1 = await db_ingest.ingest_message_event(
        chat_id=1001,
        user_id=77,
        message_id=101,
        text="first",
        username="user77",
        first_name="User",
        media_type="text",
    )
    ok2 = await db_ingest.ingest_message_event(
        chat_id=2002,
        user_id=77,
        message_id=202,
        text="second",
        username="user77",
        first_name="User",
        media_type="text",
    )

    assert ok1 is True and ok2 is True
    users = (await db_session.execute(select(User))).scalars().all()
    msgs = (await db_session.execute(select(Message))).scalars().all()
    assert len(users) == 1
    assert users[0].id == 77
    assert users[0].chat_id == 2002
    assert len(msgs) == 2


@pytest.mark.asyncio
async def test_ingest_message_event_large_negative_chat_id_is_sqlite_safe(db_session, monkeypatch):
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
        chat_id=-1001758892482,
        user_id=430831653,
        message_id=216,
        text="hello",
        username="x",
        first_name="X",
        media_type="text",
    )

    assert ok is True
    msg = (await db_session.execute(select(Message))).scalars().one()
    assert 0 <= int(msg.telegram_id) <= (1 << 63) - 1
