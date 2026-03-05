"""
Дерево связей пользователей: кто с кем общается, о чём.
Лог диалогов накапливается по дням, раз в день подводится саммари и обновляются связи.
При выключенном боте: при следующем запуске обрабатываются все пропущенные дни.
"""

import json
import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
GRAPH_JSON = DATA_DIR / "social_graph.json"
DIALOGUE_LOG_DAYS = 14  # храним сырые диалоги за последние N дней
LAST_PROCESSED_KEY = "last_processed_date"  # дата последней обработки (YYYY-MM-DD)
PAIR_SUMMARY_MAX_LEN = 1400
CONNECTION_SUMMARY_MAX_LEN = 6000


def _load() -> dict:
    if not GRAPH_JSON.exists():
        return {"dialogue_log": {}, "processed_dates": {}, "connections": {}, "realtime_cursors": {}, LAST_PROCESSED_KEY: None}
    try:
        data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"dialogue_log": {}, "processed_dates": {}, "connections": {}, "realtime_cursors": {}, LAST_PROCESSED_KEY: None}
        data.setdefault("dialogue_log", {})
        data.setdefault("processed_dates", {})
        data.setdefault("connections", {})
        data.setdefault("realtime_cursors", {})
        data.setdefault(LAST_PROCESSED_KEY, None)
        return data
    except Exception as e:
        logger.warning("Не удалось загрузить social_graph: %s", e)
        return {"dialogue_log": {}, "processed_dates": {}, "connections": {}, "realtime_cursors": {}, LAST_PROCESSED_KEY: None}


def _save(data: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=DATA_DIR) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        tmp_path.replace(GRAPH_JSON)
    except Exception as e:
        logger.warning("Не удалось сохранить social_graph: %s", e)


def _pair_key(uid1: int, uid2: int) -> str:
    """Ключ пары пользователей (сортированный)."""
    a, b = int(uid1), int(uid2)
    return f"{min(a, b)}|{max(a, b)}"


def _soft_trim(text: str, max_len: int) -> str:
    """Обрезает текст по границе слова/предложения, чтобы не рвать мысль на полуслове."""
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    chunk = t[:max_len]
    for sep in (". ", "! ", "? ", "\n", "; ", ", ", " "):
        pos = chunk.rfind(sep)
        if pos >= max_len * 0.6:
            return chunk[: pos + len(sep)].strip() + " …"
    return chunk.rstrip() + " …"


def _parse_day(d: str) -> date | None:
    try:
        return date.fromisoformat(d)
    except Exception:
        return None


def _topic_tags(text: str) -> list[str]:
    t = (text or "").lower()
    topic_map = {
        "technical": ("api", "сервер", "код", "ошиб", "инфра", "модел", "grok", "бот", "скрипт", "техн"),
        "work": ("работ", "задач", "срок", "проект", "релиз", "команд", "собес", "резюме"),
        "politics": ("путин", "полит", "выбор", "макрон", "зеленск", "трамп", "росси"),
        "humor": ("мем", "шут", "ирон", "рж", "ахах", "лол"),
        "personal": ("днюх", "подар", "семь", "друз", "отношен", "личн"),
    }
    out = [tag for tag, kws in topic_map.items() if any(k in t for k in kws)]
    return out or ["general"]


def _tone_tag(text: str) -> str:
    t = (text or "").lower()
    toxic = ("нахуй", "долбо", "пидор", "сука", "еб", "хуй", "мраз")
    conflict = ("спор", "конфликт", "срач", "агресс", "руг", "резк")
    friendly = ("дружеск", "спокой", "поддерж", "уваж", "тепл", "вежл")
    if any(x in t for x in toxic):
        return "toxic"
    if any(x in t for x in conflict):
        return "conflict"
    if any(x in t for x in friendly):
        return "friendly"
    return "neutral"


def _pair_day_counts(data: dict, ckey: str, ua: int, ub: int) -> dict[str, int]:
    by_day: dict[str, int] = {}
    for d, msgs in (data.get("dialogue_log", {}).get(ckey, {}) or {}).items():
        n = 0
        for m in msgs:
            sid = int(m.get("sender_id", 0) or 0)
            rid = int(m.get("reply_to_user_id", 0) or 0)
            if not sid or not rid:
                continue
            if (sid == ua and rid == ub) or (sid == ub and rid == ua):
                n += 1
        if n:
            by_day[d] = n
    return by_day


def _trend_delta(counts: dict[str, int]) -> float:
    today = date.today()
    seq = [counts.get((today - timedelta(days=i)).isoformat(), 0) for i in range(5, -1, -1)]
    old_avg = sum(seq[:3]) / 3.0
    new_avg = sum(seq[3:]) / 3.0
    if old_avg <= 0.01:
        return 1.0 if new_avg > 0 else 0.0
    return max(-1.0, min(3.0, (new_avg - old_avg) / old_avg))


def _connection_metrics(data: dict, ckey: str, ua: int, ub: int, summary_by_date: list[dict]) -> dict:
    counts = _pair_day_counts(data, ckey, ua, ub)
    today = date.today()
    c24 = c7 = c30 = total = 0
    first_seen = None
    last_seen = None
    for d, n in counts.items():
        dd = _parse_day(d)
        if not dd:
            continue
        total += n
        if first_seen is None or dd < first_seen:
            first_seen = dd
        if last_seen is None or dd > last_seen:
            last_seen = dd
        delta = (today - dd).days
        if 0 <= delta <= 1:
            c24 += n
        if 0 <= delta <= 6:
            c7 += n
        if 0 <= delta <= 29:
            c30 += n
    trend = _trend_delta(counts)
    joined_summaries = " ".join((e.get("summary") or "") for e in summary_by_date[-10:])
    tone = _tone_tag(joined_summaries)
    topics = _topic_tags(joined_summaries)
    confidence = 0.35 + min(0.4, c7 / 20.0) + (0.1 if len(summary_by_date) >= 3 else 0.0) + (0.1 if c24 > 0 else 0.0)
    confidence = round(max(0.05, min(0.99, confidence)), 2)
    flags: list[str] = []
    if first_seen and (today - first_seen).days <= 1:
        flags.append("new_connection")
    if trend >= 0.5 and c7 >= 6:
        flags.append("rising_activity")
    if tone in ("conflict", "toxic") and c24 >= 4:
        flags.append("toxicity_spike")
    return {
        "message_count_total": total,
        "message_count_24h": c24,
        "message_count_7d": c7,
        "message_count_30d": c30,
        "trend_delta": round(trend, 2),
        "tone": tone,
        "topics": topics,
        "confidence": confidence,
        "first_seen_at": first_seen.isoformat() if first_seen else "",
        "last_seen_at": last_seen.isoformat() if last_seen else "",
        "alert_flags": flags,
    }


def _merge_connection_entry(
    data: dict,
    ckey: str,
    pk: str,
    day: str,
    source: str,
    summary: str,
    pair_msgs_count: int,
) -> dict:
    ua, ub = pk.split("|")
    prev = (data.get("connections", {}).get(ckey, {}) or {}).get(pk, {})
    summary_by_date = list(prev.get("summary_by_date") or [])
    summary_by_date.append({
        "date": day,
        "source": source,
        "summary": summary,
        "message_count": int(pair_msgs_count),
    })
    # Дедуп и ограничение истории.
    compact: list[dict] = []
    seen = set()
    for e in summary_by_date:
        key = f"{e.get('date')}|{e.get('source')}|{e.get('summary')}"
        if key in seen:
            continue
        seen.add(key)
        compact.append(e)
    summary_by_date = compact[-60:]
    summary_lines = []
    for e in summary_by_date:
        tag = " live" if e.get("source") == "live" else ""
        summary_lines.append(f"[{e.get('date')}{tag}] {e.get('summary', '')}".strip())
    merged_summary = "\n".join(summary_lines)[-CONNECTION_SUMMARY_MAX_LEN:]
    entry = {
        "user_a": int(ua),
        "user_b": int(ub),
        "summary": merged_summary,
        "summary_by_date": summary_by_date,
        "last_updated": day,
        "message_count": prev.get("message_count", 0) + pair_msgs_count,
    }
    entry.update(_connection_metrics(data, ckey, int(ua), int(ub), summary_by_date))
    return entry


def append_dialogue_message(
    chat_id: int,
    sender_id: int,
    text: str,
    reply_to_user_id: int | None = None,
    sender_name: str = "",
    chat_title: str = "",
) -> None:
    """
    Добавляет сообщение в лог диалогов. Вызывается при каждом сообщении в группе.
    reply_to_user_id — кому адресован ответ (из message.reply_to_message.from_user.id).
    """
    if not text or not (text := text.strip()):
        return
    if sender_id == reply_to_user_id:
        return  # не считаем ответ самому себе
    data = _load()
    ckey = str(int(chat_id))
    if ckey not in data["dialogue_log"]:
        data["dialogue_log"][ckey] = {}
    today = date.today().isoformat()
    if today not in data["dialogue_log"][ckey]:
        data["dialogue_log"][ckey][today] = []
    data["dialogue_log"][ckey][today].append({
        "sender_id": int(sender_id),
        "text": text[:300],
        "reply_to_user_id": int(reply_to_user_id) if reply_to_user_id else None,
        "sender_name": (sender_name or "")[:50],
    })
    # Ограничиваем размер лога: удаляем старые дни
    if ckey in data["dialogue_log"]:
        days = sorted(data["dialogue_log"][ckey].keys())
        cutoff = (date.today() - timedelta(days=DIALOGUE_LOG_DAYS)).isoformat()
        for d in days:
            if d < cutoff:
                del data["dialogue_log"][ckey][d]
    _save(data)


def _get_unprocessed_dates(data: dict) -> list[tuple[str, str]]:
    """Возвращает список (chat_id, date) для необработанных дней."""
    result = []
    today = date.today().isoformat()
    for ckey, days in data.get("dialogue_log", {}).items():
        processed = set(data.get("processed_dates", {}).get(ckey, []))
        for d in days:
            if d < today and d not in processed:
                result.append((ckey, d))
    return result


def _summarize_dialogue_pair(messages: list[dict], user_names: dict[str, str]) -> str:
    """Саммари диалога между парой через ИИ."""
    if not messages:
        return ""
    try:
        from ai_analyzer import get_client
        import os
        client = get_client()
        lines = []
        for m in messages:
            s = m.get("sender_name") or str(m.get("sender_id", ""))
            r = m.get("reply_to_user_id")
            to = user_names.get(str(r), str(r)) if r else "всем"
            lines.append(f"{s} -> {to}: {m.get('text', '')[:150]}")
        block = "\n".join(lines[-30:])  # последние 30 сообщений
        prompt = """По диалогу между двумя участниками чата за день сделай краткое саммари (2-4 предложения):
- О чём общались, какие темы
- Тон (дружеский, спор, обмен мнениями и т.п.)
- Кто кому чаще отвечал

Только саммари, без преамбул."""
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": block},
            ],
            temperature=0.3,
            max_tokens=300,
        )
        raw = (response.choices[0].message.content or "").strip()
        return _soft_trim(raw, PAIR_SUMMARY_MAX_LEN) if raw else ""
    except Exception as e:
        logger.warning("Ошибка саммари диалога: %s", e)
        return ""


def process_pending_days(user_display_names: dict[str, str] | None = None) -> int:
    """
    Обрабатывает все необработанные дни: саммари диалогов и обновление связей.
    user_display_names: {user_id: display_name} для подстановки имён. Если None — загружаем из user_stats.
    Возвращает количество обработанных (chat_id, date) пар.
    """
    data = _load()
    pending = _get_unprocessed_dates(data)
    if not pending:
        return 0
    if user_display_names is None:
        try:
            from user_stats import get_user_display_names
            names = get_user_display_names()
        except Exception:
            names = {}
    else:
        names = user_display_names
    processed_count = 0
    for ckey, day in sorted(pending):
        try:
            msgs = data["dialogue_log"].get(ckey, {}).get(day, [])
            if not msgs:
                data.setdefault("processed_dates", {})
                if ckey not in data["processed_dates"]:
                    data["processed_dates"][ckey] = []
                if day not in data["processed_dates"][ckey]:
                    data["processed_dates"][ckey].append(day)
                processed_count += 1
                continue
            # Группируем по парам (sender, reply_to)
            pairs: dict[str, list[dict]] = {}
            for m in msgs:
                rid = m.get("reply_to_user_id")
                sid = m.get("sender_id")
                if rid and sid and sid != rid:
                    pk = _pair_key(sid, rid)
                    if pk not in pairs:
                        pairs[pk] = []
                    pairs[pk].append(m)
            # Обновляем связи
            data.setdefault("connections", {})
            if ckey not in data["connections"]:
                data["connections"][ckey] = {}
            for pk, pair_msgs in pairs.items():
                if len(pair_msgs) < 2:
                    continue  # мало для саммари
                summary = _summarize_dialogue_pair(pair_msgs, names)
                if not summary:
                    continue
                data["connections"][ckey][pk] = _merge_connection_entry(
                    data=data,
                    ckey=ckey,
                    pk=pk,
                    day=day,
                    source="daily",
                    summary=summary,
                    pair_msgs_count=len(pair_msgs),
                )
            data.setdefault("processed_dates", {})
            if ckey not in data["processed_dates"]:
                data["processed_dates"][ckey] = []
            if day not in data["processed_dates"][ckey]:
                data["processed_dates"][ckey].append(day)
            processed_count += 1
        except Exception as e:
            logger.warning("Ошибка обработки дня %s чата %s: %s", day, ckey, e)
    data[LAST_PROCESSED_KEY] = date.today().isoformat()
    _save(data)
    return processed_count


def process_realtime_updates(
    user_display_names: dict[str, str] | None = None,
    min_new_messages: int = 4,
) -> int:
    """
    Инкрементально обновляет дерево связей по сообщениям текущего дня.
    Обновляет только пары, у которых накопилось минимум min_new_messages новых реплик.
    Возвращает число обновлённых связей.
    """
    data = _load()
    today = date.today().isoformat()
    min_new = max(1, int(min_new_messages))

    if user_display_names is None:
        try:
            from user_stats import get_user_display_names
            names = get_user_display_names()
        except Exception:
            names = {}
    else:
        names = user_display_names

    updated = 0
    data.setdefault("connections", {})
    data.setdefault("realtime_cursors", {})

    for ckey, days in (data.get("dialogue_log") or {}).items():
        msgs = (days or {}).get(today, [])
        if not msgs:
            continue

        pairs: dict[str, list[dict]] = {}
        for m in msgs:
            rid = m.get("reply_to_user_id")
            sid = m.get("sender_id")
            if rid and sid and sid != rid:
                pk = _pair_key(sid, rid)
                pairs.setdefault(pk, []).append(m)

        if not pairs:
            continue

        data["connections"].setdefault(ckey, {})
        chat_cursor = data["realtime_cursors"].setdefault(ckey, {})

        for pk, pair_msgs in pairs.items():
            if len(pair_msgs) < 2:
                continue
            prev_n = int(chat_cursor.get(pk, 0) or 0)
            if prev_n > len(pair_msgs):
                prev_n = 0
            if (len(pair_msgs) - prev_n) < min_new:
                continue

            summary = _summarize_dialogue_pair(pair_msgs, names)
            if not summary:
                continue
            data["connections"][ckey][pk] = _merge_connection_entry(
                data=data,
                ckey=ckey,
                pk=pk,
                day=today,
                source="live",
                summary=summary,
                pair_msgs_count=len(pair_msgs),
            )
            chat_cursor[pk] = len(pair_msgs)
            updated += 1

    if updated > 0:
        _save(data)
    return updated


def get_connections(chat_id: int | None = None) -> list[dict]:
    """
    Возвращает список связей для отображения в админке.
    chat_id=None — все чаты (объединённо).
    """
    data = _load()
    conn = data.get("connections", {})
    result = []
    for ckey, pairs in conn.items():
        if chat_id is not None and str(int(chat_id)) != ckey:
            continue
        for pk, v in pairs.items():
            ua = int(v.get("user_a", 0) or 0)
            ub = int(v.get("user_b", 0) or 0)
            summary_by_date = list(v.get("summary_by_date") or [])
            metrics = {}
            if ua and ub and ("message_count_7d" not in v or "tone" not in v or "confidence" not in v):
                metrics = _connection_metrics(data, ckey, ua, ub, summary_by_date)
            result.append({
                "chat_id": int(ckey),
                "user_a": ua or v.get("user_a"),
                "user_b": ub or v.get("user_b"),
                "summary": v.get("summary", ""),
                "summary_by_date": summary_by_date,
                "last_updated": v.get("last_updated", ""),
                "message_count": v.get("message_count", 0),
                "message_count_total": v.get("message_count_total", metrics.get("message_count_total", v.get("message_count", 0))),
                "message_count_24h": v.get("message_count_24h", metrics.get("message_count_24h", 0)),
                "message_count_7d": v.get("message_count_7d", metrics.get("message_count_7d", 0)),
                "message_count_30d": v.get("message_count_30d", metrics.get("message_count_30d", 0)),
                "trend_delta": v.get("trend_delta", metrics.get("trend_delta", 0)),
                "tone": v.get("tone", metrics.get("tone", "neutral")),
                "topics": list(v.get("topics") or metrics.get("topics") or []),
                "confidence": v.get("confidence", metrics.get("confidence", 0.0)),
                "first_seen_at": v.get("first_seen_at", metrics.get("first_seen_at", "")),
                "last_seen_at": v.get("last_seen_at", metrics.get("last_seen_at", "")),
                "alert_flags": list(v.get("alert_flags") or metrics.get("alert_flags") or []),
            })
    return result


def get_user_graph_context(user_id: int, chat_id: int | None = None, limit: int = 3) -> str:
    """Короткий контекст по связям/темам пользователя для QOD и персонализации."""
    uid = int(user_id)
    rows = [r for r in get_connections(chat_id) if int(r.get("user_a", 0) or 0) == uid or int(r.get("user_b", 0) or 0) == uid]
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: int(r.get("message_count_7d", 0) or 0), reverse=True)[: max(1, int(limit))]
    lines = []
    for r in rows:
        peer = int(r.get("user_b", 0) or 0) if int(r.get("user_a", 0) or 0) == uid else int(r.get("user_a", 0) or 0)
        topics = ", ".join((r.get("topics") or [])[:3]) or "general"
        tone = r.get("tone", "neutral")
        lines.append(f"- связь с {peer}: tone={tone}, темы={topics}, 7d={r.get('message_count_7d', 0)}")
    return "Контекст по связям пользователя:\n" + "\n".join(lines)


def get_conflict_forecast(chat_id: int | None = None, limit: int = 12) -> list[dict]:
    """Прогноз потенциальной эскалации конфликтов (только для админки)."""
    rows = get_connections(chat_id)
    out = []
    for r in rows:
        tone = str(r.get("tone", "neutral") or "neutral")
        trend = float(r.get("trend_delta", 0) or 0)
        c24 = int(r.get("message_count_24h", 0) or 0)
        flags = set(r.get("alert_flags") or [])
        risk = 0.0
        if tone == "toxic":
            risk += 0.55
        elif tone == "conflict":
            risk += 0.35
        if trend > 0:
            risk += min(0.25, trend * 0.2)
        risk += min(0.2, c24 / 20.0)
        if "toxicity_spike" in flags:
            risk += 0.2
        risk = round(min(1.0, risk), 2)
        if risk < 0.45:
            continue
        out.append({
            "chat_id": r.get("chat_id"),
            "user_a": r.get("user_a"),
            "user_b": r.get("user_b"),
            "tone": tone,
            "trend_delta": trend,
            "risk": risk,
            "message_count_24h": c24,
            "topics": list(r.get("topics") or []),
        })
    return sorted(out, key=lambda x: x["risk"], reverse=True)[: max(1, int(limit))]


def get_user_roles(chat_id: int | None = None, limit: int = 100) -> list[dict]:
    """Роли участников по графу (админ-аналитика)."""
    rows = get_connections(chat_id)
    stats: dict[int, dict] = {}
    for r in rows:
        for uid in (int(r.get("user_a", 0) or 0), int(r.get("user_b", 0) or 0)):
            if uid <= 0:
                continue
            s = stats.setdefault(uid, {"degree": 0, "toxic": 0, "conflict": 0, "friendly": 0, "topics": {}})
            s["degree"] += 1
            tone = str(r.get("tone", "neutral") or "neutral")
            if tone in s:
                s[tone] += 1
            for t in (r.get("topics") or []):
                s["topics"][t] = s["topics"].get(t, 0) + 1
    result = []
    for uid, s in stats.items():
        role = "participant"
        if s["degree"] >= 5:
            role = "connector"
        if s["toxic"] >= 2:
            role = "provocateur"
        if s["friendly"] >= 3 and s["degree"] >= 3:
            role = "mediator"
        top_topics = sorted(s["topics"].items(), key=lambda kv: kv[1], reverse=True)[:2]
        if top_topics and top_topics[0][0] == "technical":
            role = "expert"
        result.append({
            "user_id": uid,
            "role": role,
            "degree": s["degree"],
            "top_topics": [x[0] for x in top_topics],
        })
    return sorted(result, key=lambda x: x["degree"], reverse=True)[: max(1, int(limit))]


def build_chat_digest(chat_id: int, period_days: int = 1, max_items: int = 8) -> str:
    """Строит содержательный дайджест чата на основе связей, тем и тональности."""
    cid = int(chat_id)
    days = max(1, int(period_days or 1))

    # Для короткого окна берём 24h, для среднего — 7d, иначе 30d.
    metric_key = "message_count_24h" if days <= 1 else ("message_count_7d" if days <= 7 else "message_count_30d")
    rows_all = [r for r in get_connections(cid) if int(r.get("message_count_30d", 0) or 0) > 0]
    if not rows_all:
        return "Дайджест: пока недостаточно данных по связям."

    rows = [r for r in rows_all if int(r.get(metric_key, 0) or 0) > 0]
    if not rows:
        rows = rows_all

    from user_stats import get_user_display_names
    names = get_user_display_names()

    tone_ru = {
        "neutral": "нейтральный",
        "friendly": "дружелюбный",
        "conflict": "конфликтный",
        "toxic": "токсичный",
    }
    topic_ru = {
        "general": "общее",
        "humor": "юмор",
        "personal": "личное",
        "technical": "техника/бот",
        "work": "работа",
        "politics": "политика",
    }

    def _display(uid) -> str:
        key = str(int(uid or 0))
        return names.get(key) or key

    def _activity(r: dict) -> int:
        return int(r.get(metric_key, 0) or 0)

    def _latest_summary(r: dict, max_len: int = 170) -> str:
        by_date = list(r.get("summary_by_date") or [])
        if by_date:
            s = str((by_date[-1].get("summary") or "")).strip()
        else:
            lines = [ln.strip() for ln in str(r.get("summary", "") or "").splitlines() if ln.strip()]
            s = lines[-1] if lines else ""
            if s.startswith("[") and "] " in s:
                s = s.split("] ", 1)[1].strip()
        s = " ".join(s.split()).strip()
        if not s:
            return ""
        return _soft_trim(s, max_len)

    topic_counts: dict[str, int] = {}
    tone_counts: dict[str, int] = {}
    total_msgs = 0
    participant_counts: dict[str, int] = {}
    for r in rows:
        cnt = _activity(r)
        total_msgs += cnt
        tone = str(r.get("tone", "neutral") or "neutral")
        tone_counts[tone] = tone_counts.get(tone, 0) + cnt
        for t in (r.get("topics") or []):
            topic_counts[t] = topic_counts.get(t, 0) + 1
        ua = str(int(r.get("user_a", 0) or 0))
        ub = str(int(r.get("user_b", 0) or 0))
        participant_counts[ua] = participant_counts.get(ua, 0) + cnt
        participant_counts[ub] = participant_counts.get(ub, 0) + cnt

    top_topics_arr = sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_topics = ", ".join(f"{topic_ru.get(k, k)} ({v})" for k, v in top_topics_arr) if top_topics_arr else "общее"
    dominant_tone = sorted(tone_counts.items(), key=lambda kv: kv[1], reverse=True)[0][0] if tone_counts else "neutral"

    top_people = sorted(participant_counts.items(), key=lambda kv: kv[1], reverse=True)[:4]
    top_people_text = ", ".join(f"{_display(uid)} ({cnt})" for uid, cnt in top_people) if top_people else "—"

    top_links = sorted(rows, key=_activity, reverse=True)[: max(1, int(max_items))]
    lines = [
        f"Дайджест чата {cid} (последние ~{days} дн.)",
        f"Активные связи: {len(rows)} · сообщений в связях: {total_msgs}",
        f"Доминирующий тон: {tone_ru.get(dominant_tone, dominant_tone)}",
        f"Топ тем: {top_topics}",
        f"Самые вовлечённые участники: {top_people_text}",
        "",
        "Что происходило:",
    ]
    for r in top_links:
        ua = _display(r.get("user_a"))
        ub = _display(r.get("user_b"))
        cnt = _activity(r)
        tone = tone_ru.get(str(r.get("tone", "neutral") or "neutral"), "нейтральный")
        bullet = f"- {ua} ↔ {ub}: {cnt} сообщ. · тон: {tone}"
        latest = _latest_summary(r)
        if latest:
            bullet += f"\n  ↳ {latest}"
        lines.append(bullet)
    return "\n".join(lines)


def get_chats_with_connections() -> list[dict]:
    """Чаты, в которых есть связи или лог диалогов (для выбора в админке)."""
    data = _load()
    from user_stats import get_chats
    chats_map = {str(c["chat_id"]): c for c in get_chats()}
    seen = set()
    result = []
    for ckey in list(data.get("connections", {}).keys()) + list(data.get("dialogue_log", {}).keys()):
        if ckey not in seen and ckey in chats_map:
            seen.add(ckey)
            result.append(chats_map[ckey])
    return sorted(result, key=lambda x: x["chat_id"])
