from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timedelta

import social_graph
import user_stats
from db.engine import get_db
from db.models import Edge, Message, User, UserPortrait
from sqlalchemy import delete, or_, update


def user_hash(user_id: int | str, chat_id: int | str | None = None) -> str:
    """
    Stable pseudonymous identifier for analytics surfaces.
    """
    salt = str(os.getenv("USER_HASH_SALT") or os.getenv("ADMIN_SECRET_KEY") or "nopolicybot").strip()
    payload = f"{salt}|{chat_id if chat_id is not None else 'all'}|{int(user_id)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _parse_day(raw: str | None) -> date | None:
    text = str(raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _cutoff(days: int) -> date:
    safe_days = max(1, int(days or 90))
    return date.today() - timedelta(days=safe_days)


def prune_user_stats_messages(days: int = 90) -> dict:
    limit = _cutoff(days)
    data = user_stats._load() if hasattr(user_stats, "_load") else {"users": {}}
    users = (data.get("users") or {}) if isinstance(data, dict) else {}
    changed = False
    removed_messages = 0
    removed_images = 0
    removed_views = 0
    removed_bot_msgs = 0

    for row in users.values():
        if not isinstance(row, dict):
            continue
        if hasattr(user_stats, "_ensure_messages_by_chat"):
            try:
                if user_stats._ensure_messages_by_chat(row):
                    changed = True
            except Exception:
                pass

        by_chat = row.get("messages_by_chat") or {}
        for chat_key, msgs in list(by_chat.items()):
            safe_msgs = list(msgs or [])
            kept = []
            for msg in safe_msgs:
                when = _parse_day((msg or {}).get("date"))
                if when is not None and when < limit:
                    removed_messages += 1
                    continue
                kept.append(msg)
            if len(kept) != len(safe_msgs):
                changed = True
            if kept:
                by_chat[chat_key] = kept
            else:
                by_chat.pop(chat_key, None)
                changed = True

        daily = list(row.get("daily_buffer") or [])
        kept_daily = []
        for msg in daily:
            when = _parse_day((msg or {}).get("date"))
            if when is not None and when < limit:
                removed_messages += 1
                continue
            kept_daily.append(msg)
        if len(kept_daily) != len(daily):
            row["daily_buffer"] = kept_daily
            changed = True

        bot_msgs = list(row.get("messages_to_bot_buffer") or [])
        kept_bot = []
        for msg in bot_msgs:
            when = _parse_day((msg or {}).get("date"))
            if when is not None and when < limit:
                removed_bot_msgs += 1
                continue
            kept_bot.append(msg)
        if len(kept_bot) != len(bot_msgs):
            row["messages_to_bot_buffer"] = kept_bot
            changed = True

        images = list(row.get("images_archive") or [])
        kept_images = []
        for img in images:
            when = _parse_day((img or {}).get("date"))
            if when is not None and when < limit:
                removed_images += 1
                continue
            kept_images.append(img)
        if len(kept_images) != len(images):
            row["images_archive"] = kept_images
            changed = True

        views = list(row.get("close_attention_views") or [])
        kept_views = []
        for item in views:
            when = _parse_day((item or {}).get("date"))
            if when is not None and when < limit:
                removed_views += 1
                continue
            kept_views.append(item)
        if len(kept_views) != len(views):
            row["close_attention_views"] = kept_views
            changed = True

    if changed and hasattr(user_stats, "_save"):
        user_stats._save(data)

    return {
        "ok": True,
        "retention_days": int(days),
        "removed_messages": int(removed_messages),
        "removed_messages_to_bot": int(removed_bot_msgs),
        "removed_images": int(removed_images),
        "removed_close_attention_views": int(removed_views),
    }


def prune_social_graph_dialogue(days: int = 90) -> dict:
    limit = _cutoff(days)
    data = social_graph._load() if hasattr(social_graph, "_load") else {"dialogue_log": {}}
    dialogue = (data.get("dialogue_log") or {}) if isinstance(data, dict) else {}
    changed = False
    removed_messages = 0
    removed_days = 0

    for chat_key, by_day in list(dialogue.items()):
        day_map = by_day if isinstance(by_day, dict) else {}
        for d in list(day_map.keys()):
            when = _parse_day(d)
            if when is None:
                continue
            if when < limit:
                removed_messages += len(day_map.get(d) or [])
                day_map.pop(d, None)
                removed_days += 1
                changed = True
        if not day_map:
            dialogue.pop(chat_key, None)
            changed = True

    processed = data.get("processed_dates") or {}
    for chat_key, days_map in list(processed.items()):
        if not isinstance(days_map, dict):
            continue
        for d in list(days_map.keys()):
            when = _parse_day(d)
            if when is not None and when < limit:
                days_map.pop(d, None)
                changed = True
        if not days_map:
            processed.pop(chat_key, None)
            changed = True

    if changed and hasattr(social_graph, "_save"):
        social_graph._save(data)

    return {
        "ok": True,
        "retention_days": int(days),
        "removed_messages": int(removed_messages),
        "removed_days": int(removed_days),
    }


async def prune_db_messages(days: int = 90) -> dict:
    safe_days = max(1, int(days or 90))
    since = datetime.utcnow() - timedelta(days=safe_days)
    async with get_db() as session:
        result = await session.execute(delete(Message).where(Message.sent_at < since))
        removed = int(result.rowcount or 0)
    return {
        "ok": True,
        "retention_days": int(safe_days),
        "removed_messages": int(removed),
    }


async def run_retention_once(days: int = 90) -> dict:
    safe_days = max(1, int(days or 90))
    db_result = await prune_db_messages(safe_days)
    json_result = prune_user_stats_messages(safe_days)
    graph_result = prune_social_graph_dialogue(safe_days)
    total_removed = int(db_result.get("removed_messages", 0) or 0) + int(json_result.get("removed_messages", 0) or 0) + int(
        graph_result.get("removed_messages", 0) or 0
    )
    return {
        "ok": True,
        "retention_days": int(safe_days),
        "db": db_result,
        "user_stats": json_result,
        "social_graph": graph_result,
        "total_removed_messages": int(total_removed),
    }


async def _erase_user_db(user_id: int) -> dict:
    uid = int(user_id)
    async with get_db() as session:
        deleted_messages = await session.execute(delete(Message).where(Message.user_id == uid))
        redacted_replies = await session.execute(update(Message).where(Message.replied_to == uid).values(replied_to=None))
        deleted_edges = await session.execute(delete(Edge).where(or_(Edge.from_user == uid, Edge.to_user == uid)))
        deleted_portraits = await session.execute(delete(UserPortrait).where(UserPortrait.user_id == uid))
        deleted_users = await session.execute(delete(User).where(User.id == uid))
    return {
        "db_messages_deleted": int(deleted_messages.rowcount or 0),
        "db_message_reply_refs_redacted": int(redacted_replies.rowcount or 0),
        "db_edges_deleted": int(deleted_edges.rowcount or 0),
        "db_portraits_deleted": int(deleted_portraits.rowcount or 0),
        "db_users_deleted": int(deleted_users.rowcount or 0),
    }


def _erase_user_json(user_id: int) -> dict:
    uid = int(user_id)
    removed_user = False
    removed_dialogue = 0
    removed_pairs = 0
    changed_stats = False
    changed_graph = False

    data = user_stats._load() if hasattr(user_stats, "_load") else {"users": {}}
    users = (data.get("users") or {}) if isinstance(data, dict) else {}
    if str(uid) in users:
        users.pop(str(uid), None)
        changed_stats = True
        removed_user = True
    if changed_stats and hasattr(user_stats, "_save"):
        user_stats._save(data)

    gdata = social_graph._load() if hasattr(social_graph, "_load") else {"connections": {}, "dialogue_log": {}}
    connections = (gdata.get("connections") or {}) if isinstance(gdata, dict) else {}
    for chat_key, pairs in list(connections.items()):
        if not isinstance(pairs, dict):
            continue
        for pair_key, row in list(pairs.items()):
            ua = int((row or {}).get("user_a", 0) or 0)
            ub = int((row or {}).get("user_b", 0) or 0)
            if ua == uid or ub == uid:
                pairs.pop(pair_key, None)
                removed_pairs += 1
                changed_graph = True
        if not pairs:
            connections.pop(chat_key, None)
            changed_graph = True

    dialogue = (gdata.get("dialogue_log") or {}) if isinstance(gdata, dict) else {}
    for chat_key, by_day in list(dialogue.items()):
        if not isinstance(by_day, dict):
            continue
        for d, msgs in list(by_day.items()):
            src = list(msgs or [])
            kept = []
            for msg in src:
                sid = int((msg or {}).get("sender_id", 0) or 0)
                rid = int((msg or {}).get("reply_to_user_id", 0) or 0) if (msg or {}).get("reply_to_user_id") is not None else 0
                if sid == uid or rid == uid:
                    removed_dialogue += 1
                    changed_graph = True
                    continue
                kept.append(msg)
            if kept:
                by_day[d] = kept
            else:
                by_day.pop(d, None)
                changed_graph = True
        if not by_day:
            dialogue.pop(chat_key, None)
            changed_graph = True

    cursors = gdata.get("realtime_cursors") or {}
    if isinstance(cursors, dict):
        for chat_key, pair_map in list(cursors.items()):
            if not isinstance(pair_map, dict):
                continue
            for pair_key in list(pair_map.keys()):
                parts = str(pair_key or "").split("|")
                if len(parts) == 2 and (parts[0] == str(uid) or parts[1] == str(uid)):
                    pair_map.pop(pair_key, None)
                    changed_graph = True
            if not pair_map:
                cursors.pop(chat_key, None)
                changed_graph = True

    if changed_graph and hasattr(social_graph, "_save"):
        social_graph._save(gdata)

    return {
        "json_user_removed": bool(removed_user),
        "json_graph_pairs_removed": int(removed_pairs),
        "json_graph_dialogue_messages_removed": int(removed_dialogue),
    }


async def erase_user_data(user_id: int) -> dict:
    uid = int(user_id)
    db = await _erase_user_db(uid)
    js = _erase_user_json(uid)
    return {
        "ok": True,
        "user_id": int(uid),
        **db,
        **js,
    }

