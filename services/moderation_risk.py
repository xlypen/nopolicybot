from __future__ import annotations

import asyncio
from collections import Counter

import user_stats
from db.engine import get_db
from db.repositories.message_repo import MessageRepository
from services.storage_cutover import get_storage_mode
from services.tone_analyzer import analyze_tone_context


async def _texts_from_db(chat_id: int, days: int = 30) -> list[str]:
    async with get_db() as session:
        repo = MessageRepository(session)
        rows = await repo.get_by_period(chat_id=chat_id, days=days)
    return [str(m.text) for m in rows if getattr(m, "text", None)]


def build_moderation_risk(chat_id: int | None = None) -> dict:
    mode = get_storage_mode()
    texts: list[str] = []
    if chat_id is not None and mode in {"db", "hybrid"}:
        try:
            texts = asyncio.run(_texts_from_db(chat_id=int(chat_id), days=30))
            if mode == "hybrid" and not texts:
                texts = []
        except Exception:
            if mode == "db":
                texts = []

    if not texts:
        data = user_stats._load()
        users = data.get("users", {}) or {}
        for _uid, u in users.items():
            by_chat = u.get("messages_by_chat") or {}
            for cid, msgs in by_chat.items():
                if chat_id is not None and str(cid) != str(chat_id):
                    continue
                for m in msgs or []:
                    text = str(m.get("text", "") or "")
                    if text:
                        texts.append(text)

    red_words = ("хуй", "пизд", "еб", "туп", "идиот")
    cnt = Counter()
    for text in texts:
        lt = text.lower()
        for w in red_words:
            if w in lt:
                cnt[w] += 1
    tone = analyze_tone_context(texts[-300:])
    return {
        "risk_messages_7d": int(sum(cnt.values())),
        "top_red_flags": [{"word": w, "count": c} for w, c in cnt.most_common(10)],
        "critical_pairs": [],
        "high_pairs": [],
        "newcomers_risky": [],
        "tone_context": tone,
        "tone_risk_score_pct": round(min(100.0, max(0.0, tone.get("negative_share_pct", 0.0))), 1),
    }
