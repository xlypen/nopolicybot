from __future__ import annotations

from datetime import date, timedelta

import user_stats
from services.tone_analyzer import analyze_tone_context


def _daily_activity(chat_id: int | None = None, days: int = 30) -> list[dict]:
    data = user_stats._load()
    users = data.get("users", {}) or {}
    today = date.today()
    rows = {today - timedelta(days=i): 0 for i in range(days)}
    texts = []
    for _uid, u in users.items():
        by_chat = u.get("messages_by_chat") or {}
        for cid, msgs in by_chat.items():
            if chat_id is not None and str(cid) != str(chat_id):
                continue
            for m in msgs or []:
                raw = str(m.get("date", "") or "")[:10]
                try:
                    d = date.fromisoformat(raw)
                except Exception:
                    continue
                if d in rows:
                    rows[d] += 1
                txt = str(m.get("text", "") or "").strip()
                if txt:
                    texts.append(txt)
    out = [{"date": d.isoformat(), "count": c} for d, c in sorted(rows.items())]
    return out, texts


def build_community_health(chat_id: int | None = None) -> dict:
    counts, texts = _daily_activity(chat_id, days=30)
    dau = counts[-1]["count"] if counts else 0
    wau = sum(c["count"] for c in counts[-7:])
    mau = sum(c["count"] for c in counts[-30:])
    avg = sum(c["count"] for c in counts) / max(1, len(counts))
    forecast = [
        {"date": (date.today() + timedelta(days=i + 1)).isoformat(), "forecast": round(avg, 1), "lower": round(max(0.0, avg * 0.8), 1), "upper": round(avg * 1.2, 1)}
        for i in range(7)
    ]
    return {
        "dau": int(dau),
        "wau": int(wau),
        "mau": int(mau),
        "stickiness": round((float(dau) / float(mau)) if mau else 0.0, 4),
        "retention_rows": [],
        "response_time": {"available": False},
        "daily_counts": counts,
        "forecast_7d": {"model": "linear", "points": forecast},
        "tone_context": analyze_tone_context(texts[-300:]),
    }
