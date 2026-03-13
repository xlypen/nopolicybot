from __future__ import annotations

import asyncio
from datetime import date, timedelta

import user_stats
from db.engine import get_db
from db.repositories.message_repo import MessageRepository
from services.storage_cutover import get_storage_mode
from services.tone_analyzer import analyze_tone_context


async def _daily_activity_db(chat_id: int, days: int = 30) -> tuple[list[dict], list[str]]:
    async with get_db() as session:
        repo = MessageRepository(session)
        daily = await repo.get_daily_counts(chat_id=chat_id, days=days)
        rows_map: dict[str, int] = {}
        for row in daily:
            raw_date = row.get("date")
            key = str(raw_date)
            rows_map[key] = int(row.get("count", 0) or 0)
        msgs = await repo.get_by_period(chat_id=chat_id, days=days)
        texts = [str(m.text) for m in msgs if getattr(m, "text", None)]

    today = date.today()
    out = []
    for i in range(days):
        d = (today - timedelta(days=days - i - 1)).isoformat()
        out.append({"date": d, "count": int(rows_map.get(d, 0))})
    return out, texts


def _daily_activity(chat_id: int | None = None, days: int = 30) -> tuple[list[dict], list[str]]:
    mode = get_storage_mode()
    if chat_id is not None and mode in {"db", "hybrid"}:
        try:
            out, texts = asyncio.run(_daily_activity_db(chat_id=int(chat_id), days=days))
            has_data = any(int(x.get("count", 0) or 0) > 0 for x in out)
            if has_data or mode == "db":
                return out, texts
        except Exception:
            if mode == "db":
                today = date.today()
                return (
                    [{"date": (today - timedelta(days=days - i - 1)).isoformat(), "count": 0} for i in range(days)],
                    [],
                )

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
