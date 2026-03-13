from __future__ import annotations

from collections import Counter

import user_stats
from services.tone_analyzer import analyze_tone_context


def build_moderation_risk(chat_id: int | None = None) -> dict:
    data = user_stats._load()
    users = data.get("users", {}) or {}
    red_words = ("хуй", "пизд", "еб", "туп", "идиот")
    cnt = Counter()
    texts: list[str] = []
    for _uid, u in users.items():
        by_chat = u.get("messages_by_chat") or {}
        for cid, msgs in by_chat.items():
            if chat_id is not None and str(cid) != str(chat_id):
                continue
            for m in msgs or []:
                text = str(m.get("text", "") or "")
                if text:
                    texts.append(text)
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
