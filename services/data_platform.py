from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from db.engine import get_db
from db.models import Edge, Message, User


def _effective_storage_mode() -> str:
    from services.storage_cutover import get_storage_mode
    return get_storage_mode()


def _json_counts() -> dict:
    users_total = 0
    messages_total = 0
    edges_total = 0
    chats = set()
    try:
        import user_stats

        data = user_stats._load() if hasattr(user_stats, "_load") else {"users": {}}
        users = data.get("users", {}) or {}
        users_total = len(users)
        for user in users.values():
            by_chat = user.get("messages_by_chat") or {}
            for chat_id, rows in by_chat.items():
                chats.add(str(chat_id))
                messages_total += len(rows or [])
    except Exception:
        pass

    try:
        import social_graph

        rows = social_graph.get_connections(None) or []
        edges_total = len(rows)
        for row in rows:
            cid = row.get("chat_id")
            if cid is not None:
                chats.add(str(cid))
    except Exception:
        pass

    return {
        "users": int(users_total),
        "messages": int(messages_total),
        "edges": int(edges_total),
        "chats": len(chats),
    }


async def _db_counts() -> dict:
    async with get_db() as session:
        users = int((await session.execute(select(func.count(User.id)))).scalar() or 0)
        messages = int((await session.execute(select(func.count(Message.id)))).scalar() or 0)
        edges = int((await session.execute(select(func.count(Edge.id)))).scalar() or 0)
        users_chats = int((await session.execute(select(func.count(func.distinct(User.chat_id))))).scalar() or 0)
        messages_chats = int((await session.execute(select(func.count(func.distinct(Message.chat_id))))).scalar() or 0)
        edges_chats = int((await session.execute(select(func.count(func.distinct(Edge.chat_id))))).scalar() or 0)
    return {
        "users": users,
        "messages": messages,
        "edges": edges,
        "chats": max(users_chats, messages_chats, edges_chats),
    }


async def get_db_counts_async() -> dict:
    return await _db_counts()


def _safe_db_counts() -> tuple[dict, str | None]:
    """Get DB counts from sync or async context. When event loop is running, run in thread to avoid nesting."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def _run_db_counts() -> tuple[dict, str | None]:
        try:
            counts = asyncio.run(_db_counts())
            return counts, None
        except Exception as e:
            return {"users": 0, "messages": 0, "edges": 0, "chats": 0}, str(e)

    if loop is not None:
        # In async context: run in thread to avoid "cannot run event loop while another is running"
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_db_counts)
            return future.result(timeout=15)
    return _run_db_counts()


def export_snapshot(*args, **kwargs) -> dict:
    marker = Path(".sqlite_migrated_from_json")
    json_counts = _json_counts()
    db_counts, db_error = _safe_db_counts()
    delta = {k: int(db_counts.get(k, 0)) - int(json_counts.get(k, 0)) for k in ("users", "messages", "edges", "chats")}
    storage_primary = _effective_storage_mode()
    return {
        "ok": True,
        "storage_primary": storage_primary,
        "marker_present": marker.exists(),
        "json": json_counts,
        "db": db_counts,
        "delta_db_minus_json": delta,
        "db_error": db_error,
    }
