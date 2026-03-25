"""Страница /me: профиль участника из основной БД (DATABASE_URL) + существующий user_stats."""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import date
from typing import Any

from sqlalchemy import func, select

logger = logging.getLogger(__name__)


def _orm_db_configured() -> bool:
    try:
        from db.engine import DATABASE_URL

        url = (DATABASE_URL or "").strip().lower()
        return bool(url)
    except Exception:
        return False


def _participant_row_exists(session, user_id: int) -> bool:
    from db.models import Edge, User, UserProfile

    if session.get(User, user_id) is not None:
        return True
    if session.get(UserProfile, user_id) is not None:
        return True
    eid = session.execute(
        select(Edge.id).where((Edge.from_user == user_id) | (Edge.to_user == user_id)).limit(1)
    ).scalar_one_or_none()
    return eid is not None


def participant_visible_in_main_db(user_id: int) -> bool:
    if not _orm_db_configured():
        return False
    try:
        from db.sync_engine import sync_session_scope

        with sync_session_scope() as session:
            return _participant_row_exists(session, user_id)
    except Exception as e:
        logger.debug("participant_visible_in_main_db: %s", e)
        return False


def merge_main_db_profile(user_id: int, base: dict[str, Any]) -> dict[str, Any]:
    """Накладывает users / user_profiles / count(messages) из основной БД на словарь профиля."""
    from db.models import Message, User, UserProfile
    from db.sync_engine import sync_session_scope

    out = deepcopy(base)
    try:
        with sync_session_scope() as session:
            prof = session.get(UserProfile, user_id)
            if prof is not None and isinstance(prof.profile_json, dict):
                for k, v in prof.profile_json.items():
                    if k == "stats" and isinstance(v, dict):
                        out.setdefault("stats", {})
                        if isinstance(out["stats"], dict):
                            out["stats"].update(v)
                    elif k == "user_id":
                        continue
                    else:
                        out[k] = deepcopy(v) if isinstance(v, (dict, list)) else v

            row = session.get(User, user_id)
            if row is not None:
                parts = [row.first_name or "", row.last_name or ""]
                dn = " ".join(str(p).strip() for p in parts if p and str(p).strip()).strip()
                if not dn and row.username:
                    dn = "@" + str(row.username).strip().lstrip("@")
                if dn:
                    out["display_name"] = dn
                out.setdefault("stats", {})
                if isinstance(out["stats"], dict):
                    out["stats"]["political_messages"] = int(row.political_messages or 0)
                    out["stats"]["warnings_received"] = int(row.warnings_received or 0)

            cnt = session.execute(
                select(func.count()).select_from(Message).where(Message.user_id == user_id)
            ).scalar_one()
            if cnt is not None and int(cnt) > 0:
                out.setdefault("stats", {})
                if isinstance(out["stats"], dict):
                    out["stats"]["total_messages"] = int(cnt)
    except Exception as e:
        logger.debug("merge_main_db_profile: %s", e)
    return out


def build_participant_user_for_me(user_id: int, users_blob: dict[str, Any]) -> dict[str, Any] | None:
    """
    Словарь пользователя для шаблона participant_me.
    None — пользователь не найден ни в JSON, ни в основной БД.
    """
    import user_stats

    users = (users_blob or {}).get("users") or {}
    json_u = users.get(str(user_id))
    in_db = participant_visible_in_main_db(user_id)
    if json_u is None and not in_db:
        return None

    display_name = ""
    if isinstance(json_u, dict):
        display_name = str(json_u.get("display_name") or "").strip()

    u = user_stats.get_user(user_id, display_name)
    if in_db:
        u = merge_main_db_profile(user_id, u)
    return u


def load_user_messages_from_main_db(user_id: int, chat_id: int | None, limit: int) -> list[dict[str, Any]]:
    """Сообщения из таблицы messages (основная БД) в формате архива для deep portrait."""
    from db.models import Message
    from db.sync_engine import sync_session_scope

    out: list[dict[str, Any]] = []
    try:
        with sync_session_scope() as session:
            q = (
                select(Message.text, Message.sent_at, Message.chat_id)
                .where(Message.user_id == user_id)
                .where(Message.text.isnot(None))
                .where(Message.text != "")
            )
            if chat_id is not None:
                q = q.where(Message.chat_id == chat_id)
            q = q.order_by(Message.sent_at.asc()).limit(max(1, int(limit)))
            for row in session.execute(q).all():
                text, sent_at, cid = row
                date_str = str(sent_at)[:10] if sent_at else ""
                t = str(text or "").strip()
                if not t:
                    continue
                item: dict[str, Any] = {"text": t, "date": date_str}
                if cid is not None:
                    item["chat_id"] = int(cid)
                out.append(item)
    except Exception as e:
        logger.debug("load_user_messages_from_main_db: %s", e)
    return out


def messages_for_deep_portrait(user_id: int, chat_id: int | None) -> list[dict[str, Any]]:
    """Архив из storage/JSON; если пусто — последние сообщения из ORM messages."""
    import user_stats

    limit = int(getattr(user_stats, "MESSAGES_ARCHIVE_LIMIT", 1000) or 1000)
    msgs = user_stats.get_user_messages_archive(user_id, chat_id)
    if msgs:
        return msgs
    return load_user_messages_from_main_db(user_id, chat_id, limit)


def merge_portrait_into_main_db_user_profile(user_id: int, portrait: str, rank: str) -> None:
    """Дублирует текстовый портрет в user_profiles (Postgres/SQLite ORM), чтобы /me сразу видел актуал."""
    from db.models import UserProfile
    from db.sync_engine import sync_session_scope

    allowed = {"loyal", "neutral", "opposition", "unknown"}
    rnk = rank if rank in allowed else "neutral"
    try:
        with sync_session_scope() as session:
            row = session.get(UserProfile, user_id)
            if row is None:
                row = UserProfile(user_id=user_id, profile_json={})
                session.add(row)
            pj = dict(row.profile_json if isinstance(row.profile_json, dict) else {})
            pj["portrait"] = (portrait or "").strip()[:8000]
            pj["rank"] = rnk
            pj["portrait_updated_date"] = date.today().isoformat()
            row.profile_json = pj
    except Exception as e:
        logger.debug("merge_portrait_into_main_db_user_profile: %s", e)
