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
from services.data_platform import export_snapshot
from services.storage_cutover import apply_cutover


def _read_json_source() -> tuple[dict, dict]:
    user_stats_path = "user_stats.json" if os.path.exists("user_stats.json") else None
    graph_path = "social_graph.json" if os.path.exists("social_graph.json") else None
    us = {}
    sg = {}
    if user_stats_path:
        us = json.loads(Path(user_stats_path).read_text(encoding="utf-8"))
    if graph_path:
        sg = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    return us, sg


def _source_counts(us: dict, sg: dict) -> dict:
    users = 0
    messages = 0
    edges = 0
    if us:
        users = len((us.get("users", us) or {}))
        for payload in (us.get("users", us) or {}).values():
            by_chat = payload.get("messages_by_chat") or {}
            for msgs in by_chat.values():
                messages += len(msgs or [])
    if sg:
        edges = len(sg.get("edges", sg.get("connections", [])) or [])
    return {"users": users, "messages": messages, "edges": edges}


async def migrate(dry_run: bool = True, write_marker: bool = False):
    if not dry_run:
        await init_db()
    stats = {"users": 0, "messages": 0, "edges": 0, "errors": 0, "skipped": 0}

    us, sg = _read_json_source()
    source = _source_counts(us, sg)
    if not us and not sg:
        return {
            "ok": True,
            "mode": "dry-run" if dry_run else "migrate",
            "source": source,
            "migrated": stats,
            "validation": {"users_ok": True, "messages_ok": True, "edges_ok": True},
        }

    if us:
        async with get_db() as session:
            urepo = UserRepository(session)
            mrepo = MessageRepository(session)
            for uid, payload in (us.get("users", us) or {}).items():
                try:
                    uid_int = int(uid)
                    if not dry_run:
                        await urepo.get_or_create(uid_int, int(payload.get("chat_id", 0) or 0))
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
                                        user_id=uid_int,
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
                    chat_id = int(e.get("chat_id", 0) or 0)
                    from_user = int(e.get("from", e.get("user_a", 0)) or 0)
                    to_user = int(e.get("to", e.get("user_b", 0)) or 0)
                    if not from_user or not to_user:
                        stats["skipped"] += 1
                        continue
                    if not dry_run:
                        await erepo.upsert(
                            chat_id=chat_id,
                            from_user=from_user,
                            to_user=to_user,
                            weight_delta=float(e.get("weight", e.get("message_count_total", 1.0)) or 1.0),
                        )
                    stats["edges"] += 1
                except Exception:
                    stats["errors"] += 1
    snapshot = export_snapshot()
    db_counts = snapshot.get("db") or {}
    validation = {
        "users_ok": int(db_counts.get("users", 0)) >= int(source.get("users", 0)),
        "messages_ok": int(db_counts.get("messages", 0)) >= int(source.get("messages", 0)),
        "edges_ok": int(db_counts.get("edges", 0)) >= int(source.get("edges", 0)),
    }
    result = {
        "ok": True,
        "mode": "dry-run" if dry_run else "migrate",
        "source": source,
        "migrated": stats,
        "db_snapshot": db_counts,
        "validation": validation,
    }
    if write_marker and not dry_run and all(validation.values()):
        Path(".sqlite_migrated_from_json").write_text(
            json.dumps({"at": datetime.utcnow().isoformat(), "source": source, "db": db_counts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["marker_written"] = True
    else:
        result["marker_written"] = False
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate user_stats/social_graph JSON data to SQL database")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Only calculate migration stats, do not write DB")
    parser.add_argument("--write-marker", action="store_true", default=False, help="Write .sqlite_migrated_from_json on successful validation")
    parser.add_argument("--cutover-db", action="store_true", default=False, help="Set storage mode to db after successful migration")
    args = parser.parse_args()
    out = asyncio.run(migrate(dry_run=args.dry_run, write_marker=args.write_marker))
    if args.cutover_db and not args.dry_run and out.get("ok") and all((out.get("validation") or {}).values()):
        out["cutover"] = apply_cutover("db", force=False, reason="migrate_to_db.py --cutover-db")
    print(json.dumps(out, ensure_ascii=False, indent=2))
