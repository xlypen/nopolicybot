"""
Сегодняшние реплики чата для админки «Оживить чат».

Источник — основная БД из DATABASE_URL (PostgreSQL или SQLite): таблица ``messages``,
при пустом результате — JSON в ``dialogue_log`` (ORM), без отдельного bot.db-диалекта.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)


def load_today_messages_from_main_db(chat_id: int, *, limit: int = 250) -> list[dict[str, Any]]:
    """
    Сообщения чата за текущие сутки по UTC (как у ingest_message_event / sent_at).
    Возвращает элементы вида {sender_id, sender_name, text}.
    """
    cid = int(chat_id)
    out: list[dict[str, Any]] = []
    try:
        from db.models import Message, User
        from db.sync_engine import sync_session_scope
    except Exception as e:
        logger.debug("revive_chat_source: ORM import failed: %s", e)
        return out

    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)

    try:
        with sync_session_scope() as session:
            q = (
                select(Message.user_id, Message.text, User.first_name, User.username)
                .outerjoin(User, (User.id == Message.user_id) & (User.chat_id == Message.chat_id))
                .where(Message.chat_id == cid)
                .where(Message.sent_at >= start)
                .where(Message.sent_at < end)
                .where(Message.text.isnot(None))
                .where(Message.text != "")
                .order_by(Message.sent_at.asc())
                .limit(max(1, int(limit)))
            )
            for row in session.execute(q).all():
                uid, text, first_name, username = row
                sid = int(uid or 0)
                name = (first_name or username or (str(sid) if sid else "?")).strip()
                if not name:
                    name = str(sid) if sid else "?"
                t = str(text or "").strip()
                if t:
                    out.append({"sender_id": sid, "sender_name": name[:80], "text": t})
    except Exception as e:
        logger.warning("revive_chat_source: messages query failed chat=%s: %s", cid, e)
    return out


def load_today_dialogue_log_blob(chat_id: int, *, day: date | None = None) -> list[dict[str, Any]]:
    """Запись social_graph ``dialogue_log`` в ORM за указанный день (по умолчанию сегодня)."""
    cid = int(chat_id)
    d = day or date.today()
    day_s = d.isoformat()
    out: list[dict[str, Any]] = []
    try:
        from db.models import DialogueLog
        from db.sync_engine import sync_session_scope
    except Exception as e:
        logger.debug("revive_chat_source: DialogueLog import failed: %s", e)
        return out

    try:
        with sync_session_scope() as session:
            row = session.execute(
                select(DialogueLog.data_json).where(DialogueLog.chat_id == cid).where(DialogueLog.date == day_s)
            ).scalar_one_or_none()
        if not row:
            return out
        raw = row if isinstance(row, list) else list(row or [])
        for m in raw:
            if not isinstance(m, dict):
                continue
            sid = int(m.get("sender_id") or 0)
            name = str(m.get("sender_name") or sid or "?").strip()[:80]
            text = str(m.get("text") or "").strip()
            if text:
                out.append({"sender_id": sid, "sender_name": name, "text": text})
    except Exception as e:
        logger.warning("revive_chat_source: dialogue_log query failed chat=%s: %s", cid, e)
    return out
