#!/usr/bin/env python3
"""
Fill missing tone_score values in the messages table using LLM batch analysis.

Processes messages in batches, sending groups to the LLM for tone/sentiment
scoring on a -1.0 (very negative) to +1.0 (very positive) scale.

Uses DATABASE_URL from .env — SQLite или PostgreSQL (через sync_session_scope).

Usage:
  python scripts/fill_tone_scores.py [--batch-size 20] [--limit 0] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from ai.client import chat_complete_with_fallback, load_project_env, prefer_free_mode
from db.sync_engine import sync_database_url, sync_session_scope

load_project_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

TONE_BATCH_PROMPT = """\
Ты — аналитик тональности русскоязычных чат-сообщений.

Для каждого сообщения определи tone_score — число от -1.0 до 1.0:
  -1.0 = крайне негативное, агрессивное, оскорбительное
  -0.5 = раздражённое, недовольное
   0.0 = нейтральное, информационное
  +0.5 = дружелюбное, позитивное
  +1.0 = очень позитивное, восторженное

Учитывай мат и сленг как часть стиля (не обязательно негатив).
Короткие реакции типа "ахах", "лол" — слабый позитив (+0.2..+0.4).
Оскорбления конкретных людей — негатив.

Ответ строго JSON-массив:
[{"id": <msg_id>, "tone": <float>}, ...]

Без пояснений, только JSON."""


def fetch_unscored(limit: int = 0) -> list[tuple]:
    base = """
        SELECT id, text FROM messages
        WHERE tone_score IS NULL
          AND text IS NOT NULL AND trim(text) != ''
        ORDER BY sent_at
    """
    if limit > 0:
        stmt = text(base + " LIMIT :lim")
        params = {"lim": int(limit)}
    else:
        stmt = text(base)
        params = {}
    with sync_session_scope() as session:
        rows = session.execute(stmt, params).fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def score_batch(batch: list[tuple]) -> dict[int, float]:
    """Send a batch of (id, text) to LLM, return {id: tone_score}."""
    lines = []
    for msg_id, text in batch:
        short = text.strip()[:300]
        lines.append(f"[{msg_id}] {short}")

    user_content = "\n".join(lines)
    messages = [
        {"role": "system", "content": TONE_BATCH_PROMPT},
        {"role": "user", "content": user_content},
    ]

    raw, model_used = chat_complete_with_fallback(
        messages,
        temperature=0.1,
        max_tokens=2048,
        prefer_free=prefer_free_mode(),
    )

    if not raw:
        log.warning("Empty LLM response")
        return {}

    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        log.warning("No JSON array in response: %s...", raw[:200])
        return {}

    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        log.warning("JSON parse error: %s — raw: %s...", e, raw[:200])
        return {}

    results = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        msg_id = item.get("id")
        tone = item.get("tone")
        if msg_id is None or tone is None:
            continue
        try:
            tone = float(tone)
            tone = max(-1.0, min(1.0, tone))
            results[int(msg_id)] = round(tone, 2)
        except (ValueError, TypeError):
            continue

    return results


def update_scores(scores: dict[int, float]) -> int:
    if not scores:
        return 0
    upd = text(
        "UPDATE messages SET tone_score = :tone WHERE id = :id AND tone_score IS NULL"
    )
    n = 0
    with sync_session_scope() as session:
        for msg_id, tone in scores.items():
            res = session.execute(upd, {"tone": float(tone), "id": int(msg_id)})
            n += res.rowcount or 0
    return n


def main():
    parser = argparse.ArgumentParser(description="Fill tone_score via LLM")
    parser.add_argument("--batch-size", type=int, default=20, help="Messages per LLM call")
    parser.add_argument("--limit", type=int, default=0, help="Max messages to process (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Score but don't write to DB")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between batches")
    args = parser.parse_args()

    log.info("DB (sync): %s", sync_database_url().split("@")[-1])
    rows = fetch_unscored(args.limit)
    total = len(rows)
    log.info("Found %d unscored messages", total)

    if not total:
        return

    scored = 0
    failed = 0
    batch_num = 0

    for i in range(0, total, args.batch_size):
        batch = rows[i:i + args.batch_size]
        batch_num += 1
        log.info("Batch %d: messages %d-%d of %d", batch_num, i + 1, i + len(batch), total)

        try:
            scores = score_batch(batch)
        except Exception as e:
            log.error("Batch %d failed: %s", batch_num, e)
            failed += len(batch)
            time.sleep(args.delay * 3)
            continue

        if scores:
            if not args.dry_run:
                update_scores(scores)
            scored += len(scores)
            log.info("  Scored %d/%d (total: %d/%d)", len(scores), len(batch), scored, total)
            if args.dry_run and batch_num <= 2:
                for msg_id, tone in list(scores.items())[:3]:
                    preview = next((t for mid, t in batch if mid == msg_id), "?")
                    log.info("    [%d] %.2f  %s", msg_id, tone, preview[:80])
        else:
            failed += len(batch)
            log.warning("  Batch %d: no scores returned", batch_num)

        if i + args.batch_size < total:
            time.sleep(args.delay)

    mode = "DRY RUN" if args.dry_run else "COMMITTED"
    log.info("Done (%s). Scored: %d, Failed: %d, Total: %d", mode, scored, failed, total)


if __name__ == "__main__":
    main()
