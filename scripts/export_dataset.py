#!/usr/bin/env python3
"""
Export chat data from SQLite into ML-ready formats (JSONL, CSV).

Exports:
  data/export/messages.jsonl      — all messages with metadata
  data/export/messages.csv        — same as CSV
  data/export/edges.jsonl         — social graph edges
  data/export/profiles.jsonl      — personality profiles
  data/export/reply_pairs.jsonl   — (context, reply) pairs for seq2seq
  data/export/users.csv           — user directory

Usage:
  python scripts/export_dataset.py [--out data/export]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sqlite_storage import SqliteStorage


_storage = SqliteStorage()


def _conn():
    return _storage._conn()


def export_messages(out: Path) -> int:
    conn = _conn()
    rows = conn.execute("""
        SELECT m.id, m.telegram_id, m.chat_id, m.user_id,
               u.first_name, u.username,
               m.text, m.media_type, m.replied_to, m.sent_at,
               m.tone_score, m.risk_flags, m.mention_user_ids
        FROM messages m
        LEFT JOIN users u ON m.user_id = u.id
        ORDER BY m.sent_at, m.id
    """).fetchall()

    cols = [
        "id", "telegram_id", "chat_id", "user_id",
        "first_name", "username",
        "text", "media_type", "replied_to", "sent_at",
        "tone_score", "risk_flags", "mention_user_ids",
    ]

    with open(out / "messages.jsonl", "w", encoding="utf-8") as jf, \
         open(out / "messages.csv", "w", encoding="utf-8", newline="") as cf:
        writer = csv.DictWriter(cf, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            rec = dict(zip(cols, row))
            rec["text"] = (rec["text"] or "").strip()
            if not rec["text"]:
                continue
            jf.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            writer.writerow({k: str(v) if v is not None else "" for k, v in rec.items()})

    print(f"  messages: {len(rows)} rows -> messages.jsonl + messages.csv")
    return len(rows)


def export_edges(out: Path) -> int:
    conn = _conn()
    rows = conn.execute("""
        SELECT e.id, e.chat_id, e.from_user, e.to_user,
               u1.first_name as from_name, u2.first_name as to_name,
               e.weight, e.period_7d, e.period_30d,
               e.tone, e.topics, e.summary, e.last_updated
        FROM edges e
        LEFT JOIN users u1 ON e.from_user = u1.id
        LEFT JOIN users u2 ON e.to_user = u2.id
        ORDER BY e.weight DESC
    """).fetchall()

    cols = [
        "id", "chat_id", "from_user", "to_user",
        "from_name", "to_name",
        "weight", "period_7d", "period_30d",
        "tone", "topics", "summary", "last_updated",
    ]

    with open(out / "edges.jsonl", "w", encoding="utf-8") as f:
        for row in rows:
            rec = dict(zip(cols, row))
            if rec["topics"]:
                try:
                    rec["topics"] = json.loads(rec["topics"])
                except (json.JSONDecodeError, TypeError):
                    pass
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    print(f"  edges: {len(rows)} rows -> edges.jsonl")
    return len(rows)


def export_profiles(out: Path) -> int:
    conn = _conn()
    rows = conn.execute("""
        SELECT pp.user_id, u.first_name, u.username,
               pp.chat_id, pp.messages_analyzed, pp.confidence, pp.profile_json
        FROM personality_profiles pp
        LEFT JOIN users u ON pp.user_id = u.id
        WHERE pp.messages_analyzed > 0
        ORDER BY pp.messages_analyzed DESC
    """).fetchall()

    with open(out / "profiles.jsonl", "w", encoding="utf-8") as f:
        for row in rows:
            profile = json.loads(row[6]) if row[6] else {}
            rec = {
                "user_id": row[0],
                "first_name": row[1],
                "username": row[2],
                "chat_id": row[3],
                "messages_analyzed": row[4],
                "confidence": row[5],
                **profile,
            }
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    print(f"  profiles: {len(rows)} rows -> profiles.jsonl")
    return len(rows)


def export_users(out: Path) -> int:
    conn = _conn()
    rows = conn.execute("""
        SELECT u.id, u.chat_id, u.username, u.first_name, u.last_name,
               u.joined_at, u.last_seen, u.is_active,
               u.political_messages, u.warnings_received,
               (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.id) as msg_count
        FROM users u ORDER BY msg_count DESC
    """).fetchall()

    cols = [
        "id", "chat_id", "username", "first_name", "last_name",
        "joined_at", "last_seen", "is_active",
        "political_messages", "warnings_received", "msg_count",
    ]

    with open(out / "users.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(v) if v is not None else "" for k, v in zip(cols, row)})

    print(f"  users: {len(rows)} rows -> users.csv")
    return len(rows)


def export_reply_pairs(out: Path) -> int:
    """Build (context, reply) pairs from two sources:
    1. messages table — replied_to stores user_id; find the last message
       from that user before the reply as context.
    2. dialogue_messages table — sequential pairs via reply_to_user_id.
    """
    conn = _conn()
    pairs: list[dict] = []

    user_names: dict[int, str] = {}
    for row in conn.execute("SELECT id, first_name FROM users").fetchall():
        user_names[row[0]] = row[1] or str(row[0])

    # Source 1: messages table with replied_to (= user_id of parent author)
    all_msgs = conn.execute("""
        SELECT chat_id, user_id, text, sent_at
        FROM messages
        WHERE text IS NOT NULL AND text != ''
        ORDER BY sent_at
    """).fetchall()

    # Index: (chat_id, user_id) -> list of (text, sent_at)
    from collections import defaultdict
    import bisect

    user_msgs: dict[tuple, list[tuple]] = defaultdict(list)
    for chat_id, user_id, text, sent_at in all_msgs:
        user_msgs[(chat_id, user_id)].append((sent_at, text.strip()))

    replies_src1 = conn.execute("""
        SELECT chat_id, user_id, text, sent_at, replied_to
        FROM messages
        WHERE replied_to IS NOT NULL
          AND text IS NOT NULL AND text != ''
        ORDER BY sent_at
    """).fetchall()

    for chat_id, user_id, text, sent_at, replied_to_uid in replies_src1:
        key = (chat_id, replied_to_uid)
        history = user_msgs.get(key)
        if not history:
            continue
        times = [h[0] for h in history]
        idx = bisect.bisect_left(times, sent_at) - 1
        if idx < 0:
            continue
        ctx_time, ctx_text = history[idx]
        reply_text = text.strip()
        if not ctx_text or not reply_text:
            continue
        if user_id == replied_to_uid:
            continue
        pairs.append({
            "source": "messages",
            "context_user": user_names.get(replied_to_uid, str(replied_to_uid)),
            "context_text": ctx_text,
            "context_time": str(ctx_time),
            "reply_user": user_names.get(user_id, str(user_id)),
            "reply_text": reply_text,
            "reply_time": str(sent_at),
        })

    # Source 2: dialogue_messages (sequential, with reply_to_user_id)
    try:
        dm_rows = conn.execute("""
            SELECT d1.sender_name, d1.text, d1.date,
                   d2.sender_name, d2.text, d2.date,
                   d1.sender_id, d2.sender_id
            FROM dialogue_messages d2
            JOIN dialogue_messages d1
              ON d1.chat_id = d2.chat_id
              AND d1.sender_id = d2.reply_to_user_id
              AND d1.id = (
                SELECT MAX(id) FROM dialogue_messages dd
                WHERE dd.chat_id = d2.chat_id
                  AND dd.sender_id = d2.reply_to_user_id
                  AND dd.id < d2.id
              )
            WHERE d2.reply_to_user_id IS NOT NULL
              AND d2.text != '' AND d1.text != ''
              AND d1.sender_id != d2.sender_id
        """).fetchall()

        for row in dm_rows:
            pairs.append({
                "source": "dialogue",
                "context_user": row[0],
                "context_text": row[1].strip(),
                "context_time": str(row[2]),
                "reply_user": row[3],
                "reply_text": row[4].strip(),
                "reply_time": str(row[5]),
            })
    except Exception:
        pass

    # Deduplicate by (context_text, reply_text)
    seen = set()
    unique = []
    for p in pairs:
        key = (p["context_text"][:80], p["reply_text"][:80])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    pairs = unique

    with open(out / "reply_pairs.jsonl", "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"  reply_pairs: {len(pairs)} pairs -> reply_pairs.jsonl")
    return len(pairs)


def main():
    parser = argparse.ArgumentParser(description="Export chat data to ML-ready formats")
    parser.add_argument("--out", default="data/export", help="Output directory")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Exporting to {out.resolve()} ...")
    total = 0
    total += export_messages(out)
    total += export_edges(out)
    total += export_profiles(out)
    total += export_users(out)
    total += export_reply_pairs(out)
    print(f"\nDone. Total records exported: {total}")
    print(f"Output: {out.resolve()}")


if __name__ == "__main__":
    main()
