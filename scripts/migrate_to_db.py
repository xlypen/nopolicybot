"""Migrate JSON storages to SQL database."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.engine import get_db, init_db
from db.repositories.edge_repo import EdgeRepository
from db.repositories.message_repo import MessageRepository
from db.repositories.user_repo import UserRepository


async def migrate(dry_run: bool = True):
    if not dry_run:
        await init_db()
    stats = {"users": 0, "messages": 0, "edges": 0, "errors": 0}

    user_stats_path = "user_stats.json" if os.path.exists("user_stats.json") else None
    graph_path = "social_graph.json" if os.path.exists("social_graph.json") else None

    us = {}
    sg = {}
    if user_stats_path:
        us = json.loads(Path(user_stats_path).read_text(encoding="utf-8"))
    if graph_path:
        sg = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    if not us and not sg:
        return stats

    if us:
        async with get_db() as session:
            urepo = UserRepository(session)
            mrepo = MessageRepository(session)
            for uid, payload in (us.get("users", us) or {}).items():
                try:
                    if not dry_run:
                        await urepo.get_or_create(int(uid), int(payload.get("chat_id", 0) or 0))
                    stats["users"] += 1
                    by_chat = payload.get("messages_by_chat") or {}
                    for cid, msgs in by_chat.items():
                        for m in msgs or []:
                            try:
                                sent_at = datetime.utcnow()
                                raw = str(m.get("date", "") or "")[:19]
                                if raw:
                                    try:
                                        sent_at = datetime.fromisoformat(raw)
                                    except Exception:
                                        pass
                                if not dry_run:
                                    await mrepo.add(
                                        chat_id=int(cid),
                                        user_id=int(uid),
                                        text=str(m.get("text", "") or ""),
                                        media_type="text",
                                        sent_at=sent_at,
                                    )
                                stats["messages"] += 1
                            except Exception:
                                stats["errors"] += 1
                except Exception:
                    stats["errors"] += 1

    if sg:
        async with get_db() as session:
            erepo = EdgeRepository(session)
            for e in sg.get("edges", sg.get("connections", [])) or []:
                try:
                    if not dry_run:
                        await erepo.upsert(
                            chat_id=int(e.get("chat_id", 0) or 0),
                            from_user=int(e.get("from", e.get("user_a", 0)) or 0),
                            to_user=int(e.get("to", e.get("user_b", 0)) or 0),
                            weight_delta=float(e.get("weight", e.get("message_count_total", 1.0)) or 1.0),
                        )
                    stats["edges"] += 1
                except Exception:
                    stats["errors"] += 1
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()
    out = asyncio.run(migrate(dry_run=args.dry_run))
    print(out)
