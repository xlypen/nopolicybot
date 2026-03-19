#!/usr/bin/env python3
"""
Миграция JSON (user_stats, social_graph, bot_settings) в storage-таблицы data/bot.db.

Использование:
    cd /opt/telegram-political-monitor-bot
    python scripts/migrate_json_to_sqlite.py [--dry-run]

Заполняет: user_profiles, storage_chats, user_message_archive, dialogue_log,
storage_settings, processed_dates. Connections уже в edges (через migrate_to_db).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STORAGE_MODE", "dual")

from services.sqlite_storage import SqliteStorage, init_storage


def migrate_user_stats(st: SqliteStorage, dry_run: bool) -> dict:
    us_path = Path(__file__).resolve().parent.parent / "user_stats.json"
    if not us_path.exists():
        return {"users": 0, "chats": 0, "messages": 0}
    data = json.loads(us_path.read_text(encoding="utf-8"))
    users = data.get("users") or {}
    chats = data.get("chats") or {}
    stats = {"users": 0, "chats": 0, "messages": 0}
    if not dry_run:
        for uid, u in users.items():
            try:
                st.set_user_profile(int(uid), u)
                stats["users"] += 1
            except Exception:
                pass
        for cid, c in chats.items():
            try:
                st.upsert_chat(int(cid), c.get("title", "") or str(cid))
                stats["chats"] += 1
            except Exception:
                pass
        for uid, u in users.items():
            by_chat = u.get("messages_by_chat") or {}
            for cid, msgs in by_chat.items():
                try:
                    cid_int = int(cid) if cid != "unknown" else 0
                    for m in (msgs or [])[:1000]:
                        st.append_message(int(uid), cid_int, m.get("text", ""), m.get("date", ""), dedupe=True)
                        stats["messages"] += 1
                except Exception:
                    pass
    else:
        stats["users"] = len(users)
        stats["chats"] = len(chats)
        for u in users.values():
            for msgs in (u.get("messages_by_chat") or {}).values():
                stats["messages"] += len(msgs or [])
    return stats


def migrate_bot_settings(st: SqliteStorage, dry_run: bool) -> dict:
    path = Path(__file__).resolve().parent.parent / "bot_settings.json"
    if not path.exists():
        return {"migrated": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    to_save = {k: v for k, v in data.items() if k != "chat_settings"}
    if not dry_run and to_save:
        st.set_global_settings(to_save)
    return {"migrated": bool(to_save)}


def migrate_social_graph(st: SqliteStorage, dry_run: bool) -> dict:
    path = Path(__file__).resolve().parent.parent / "social_graph.json"
    if not path.exists():
        return {"dialogue_log": 0, "last_processed": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    stats = {"dialogue_log": 0, "last_processed": False}
    if not dry_run:
        last = data.get("last_processed_date")
        if last:
            st.set_last_processed_date(last)
            stats["last_processed"] = True
        for ckey, days in (data.get("dialogue_log") or {}).items():
            try:
                cid = int(ckey)
                for date_str, msgs in (days or {}).items():
                    for m in msgs or []:
                        st.append_dialogue_message(
                            chat_id=cid,
                            date=date_str,
                            sender_id=int(m.get("sender_id", 0) or 0),
                            sender_name=(m.get("sender_name") or "")[:50],
                            text=(m.get("text") or "")[:300],
                            reply_to_user_id=int(m["reply_to_user_id"]) if m.get("reply_to_user_id") else None,
                        )
                        stats["dialogue_log"] += 1
            except (ValueError, TypeError):
                pass
        for ckey, dates in (data.get("processed_dates") or {}).items():
            try:
                cid = int(ckey)
                for d in dates or []:
                    st.set_processed_date(cid, str(d))
            except (ValueError, TypeError):
                pass
    else:
        for days in (data.get("dialogue_log") or {}).values():
            stats["dialogue_log"] += len(days or {})
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate JSON to storage tables in data/bot.db")
    parser.add_argument("--dry-run", action="store_true", help="Only report, do not write")
    args = parser.parse_args()
    dry = args.dry_run
    print("=== migrate_json_to_sqlite ===")
    print(f"Режим: {'dry-run' if dry else 'миграция'}")
    st = init_storage()
    if not st:
        print("ОШИБКА: storage недоступен (STORAGE_MODE=json?)")
        sys.exit(1)
    us = migrate_user_stats(st, dry)
    print(f"user_stats: users={us['users']}, chats={us['chats']}, messages={us['messages']}")
    bs = migrate_bot_settings(st, dry)
    print(f"bot_settings: migrated={bs.get('migrated', False)}")
    sg = migrate_social_graph(st, dry)
    print(f"social_graph: dialogue_log={sg['dialogue_log']}, last_processed={sg['last_processed']}")
    print("=== Готово ===")


if __name__ == "__main__":
    main()
