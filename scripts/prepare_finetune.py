#!/usr/bin/env python3
"""
Convert replied_to message pairs into OpenAI fine-tune JSONL format.

Builds conversation pairs where:
  - "user" role = the context message that was replied to
  - "assistant" role = the actual reply

Supports:
  - Per-user datasets (clone a specific user's style)
  - Group dataset (collective style)
  - System prompt injection from personality profiles

Output format (OpenAI fine-tune):
  {"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}

Usage:
  python scripts/prepare_finetune.py [--out data/finetune] [--min-len 5] [--user "Вильям"]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sqlite_util import sqlite_connect

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bot.db"

DEFAULT_SYSTEM = "Ты — участник русскоязычного чата. Отвечай в стиле обычного человека в мессенджере: коротко, живо, без формальностей."


def get_conn() -> sqlite3.Connection:
    return sqlite_connect(DB_PATH)


def load_profiles(conn: sqlite3.Connection) -> dict[int, dict]:
    """Load personality profiles keyed by user_id."""
    rows = conn.execute("""
        SELECT user_id, profile_json FROM personality_profiles
        WHERE messages_analyzed > 0
    """).fetchall()
    result = {}
    for uid, pj in rows:
        try:
            result[uid] = json.loads(pj)
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def build_system_prompt(profile: dict | None, user_name: str) -> str:
    """Build a system prompt from personality profile."""
    if not profile:
        return DEFAULT_SYSTEM

    parts = [f"Ты — {user_name}, участник русскоязычного чата."]

    role = profile.get("role_in_community")
    if role:
        parts.append(f"Твоя роль в чате: {role}.")

    comm = profile.get("communication", {})
    style = comm.get("style")
    if style:
        parts.append(f"Стиль общения: {style}.")

    topics = profile.get("topics", {})
    primary = topics.get("primary", [])
    if primary:
        parts.append(f"Основные темы: {', '.join(primary[:5])}.")

    ocean = profile.get("ocean", {})
    if ocean:
        traits = []
        if ocean.get("extraversion", 0.5) > 0.65:
            traits.append("общительный")
        elif ocean.get("extraversion", 0.5) < 0.35:
            traits.append("замкнутый")
        if ocean.get("agreeableness", 0.5) < 0.35:
            traits.append("конфликтный")
        elif ocean.get("agreeableness", 0.5) > 0.65:
            traits.append("дружелюбный")
        if ocean.get("neuroticism", 0.5) > 0.65:
            traits.append("эмоциональный")
        if traits:
            parts.append(f"Черты: {', '.join(traits)}.")

    parts.append("Отвечай коротко, в стиле мессенджера.")
    return " ".join(parts)


def build_pairs(conn: sqlite3.Connection, target_user_id: int | None = None,
                min_len: int = 5, max_len: int = 500) -> list[dict]:
    """Build (context, reply) conversation pairs."""
    from collections import defaultdict
    import bisect

    user_names = {}
    for row in conn.execute("SELECT id, first_name FROM users").fetchall():
        user_names[row[0]] = row[1] or str(row[0])

    all_msgs = conn.execute("""
        SELECT chat_id, user_id, text, sent_at
        FROM messages
        WHERE text IS NOT NULL AND text != ''
        ORDER BY sent_at
    """).fetchall()

    user_msgs: dict[tuple, list[tuple]] = defaultdict(list)
    for chat_id, user_id, text, sent_at in all_msgs:
        user_msgs[(chat_id, user_id)].append((sent_at, text.strip()))

    where = "AND m.user_id = ?" if target_user_id else ""
    params = (target_user_id,) if target_user_id else ()

    replies = conn.execute(f"""
        SELECT m.chat_id, m.user_id, m.text, m.sent_at, m.replied_to
        FROM messages m
        WHERE m.replied_to IS NOT NULL
          AND m.text IS NOT NULL AND m.text != ''
          {where}
        ORDER BY m.sent_at
    """, params).fetchall()

    pairs = []
    for chat_id, user_id, text, sent_at, replied_to_uid in replies:
        if user_id == replied_to_uid:
            continue
        key = (chat_id, replied_to_uid)
        history = user_msgs.get(key)
        if not history:
            continue
        times = [h[0] for h in history]
        idx = bisect.bisect_left(times, sent_at) - 1
        if idx < 0:
            continue
        ctx_text = history[idx][1]
        reply_text = text.strip()

        if len(ctx_text) < min_len or len(reply_text) < min_len:
            continue
        if len(ctx_text) > max_len:
            ctx_text = ctx_text[:max_len]
        if len(reply_text) > max_len:
            reply_text = reply_text[:max_len]

        pairs.append({
            "user_id": user_id,
            "replied_to_uid": replied_to_uid,
            "context": ctx_text,
            "reply": reply_text,
        })

    return pairs


def write_finetune_jsonl(pairs: list[dict], system_prompt: str, path: Path) -> int:
    """Write OpenAI fine-tune JSONL format."""
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for p in pairs:
            rec = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": p["context"]},
                    {"role": "assistant", "content": p["reply"]},
                ]
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="Prepare OpenAI fine-tune JSONL")
    parser.add_argument("--out", default="data/finetune", help="Output directory")
    parser.add_argument("--min-len", type=int, default=5, help="Min text length for context/reply")
    parser.add_argument("--max-len", type=int, default=500, help="Max text length")
    parser.add_argument("--user", type=str, default=None,
                        help="Generate dataset for specific user (first_name)")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    profiles = load_profiles(conn)
    user_names = {}
    user_ids = {}
    for row in conn.execute("SELECT id, first_name FROM users").fetchall():
        user_names[row[0]] = row[1] or str(row[0])
        user_ids[row[1] or str(row[0])] = row[0]

    if args.user:
        target_uid = user_ids.get(args.user)
        if not target_uid:
            print(f"User '{args.user}' not found. Available: {', '.join(user_ids.keys())}")
            sys.exit(1)

        pairs = build_pairs(conn, target_uid, args.min_len, args.max_len)
        profile = profiles.get(target_uid)
        system = build_system_prompt(profile, args.user)
        path = out / f"finetune_{args.user}.jsonl"
        n = write_finetune_jsonl(pairs, system, path)
        print(f"User '{args.user}': {n} pairs -> {path}")
        print(f"System prompt: {system[:120]}...")
    else:
        # Group dataset
        pairs = build_pairs(conn, None, args.min_len, args.max_len)
        path = out / "finetune_group.jsonl"
        n = write_finetune_jsonl(pairs, DEFAULT_SYSTEM, path)
        print(f"Group dataset: {n} pairs -> {path}")

        # Per-user datasets for top users (>100 replies)
        user_pair_counts = defaultdict(int)
        for p in pairs:
            user_pair_counts[p["user_id"]] += 1

        for uid, count in sorted(user_pair_counts.items(), key=lambda x: -x[1]):
            if count < 50:
                continue
            name = user_names.get(uid, str(uid))
            user_pairs = [p for p in pairs if p["user_id"] == uid]
            profile = profiles.get(uid)
            system = build_system_prompt(profile, name)
            path = out / f"finetune_{name}.jsonl"
            n = write_finetune_jsonl(user_pairs, system, path)
            print(f"User '{name}': {n} pairs -> {path}")

    conn.close()
    print(f"\nOutput directory: {out.resolve()}")


if __name__ == "__main__":
    main()
