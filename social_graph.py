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


def _load() -> dict:
    if not GRAPH_JSON.exists():
        return {"dialogue_log": {}, "processed_dates": {}, "connections": {}, LAST_PROCESSED_KEY: None}
    try:
        data = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"dialogue_log": {}, "processed_dates": {}, "connections": {}, LAST_PROCESSED_KEY: None}
        data.setdefault("dialogue_log", {})
        data.setdefault("processed_dates", {})
        data.setdefault("connections", {})
        data.setdefault(LAST_PROCESSED_KEY, None)
        return data
    except Exception as e:
        logger.warning("Не удалось загрузить social_graph: %s", e)
        return {"dialogue_log": {}, "processed_dates": {}, "connections": {}, LAST_PROCESSED_KEY: None}


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
        return raw[:500] if raw else ""
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
                ua, ub = pk.split("|")
                prev = data["connections"][ckey].get(pk, {})
                prev_sum = prev.get("summary", "")
                new_sum = f"{prev_sum}\n[{day}] {summary}".strip() if prev_sum else f"[{day}] {summary}"
                data["connections"][ckey][pk] = {
                    "user_a": int(ua),
                    "user_b": int(ub),
                    "summary": new_sum[-2000:],  # ограничиваем длину
                    "last_updated": day,
                    "message_count": prev.get("message_count", 0) + len(pair_msgs),
                }
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
            result.append({
                "chat_id": int(ckey),
                "user_a": v.get("user_a"),
                "user_b": v.get("user_b"),
                "summary": v.get("summary", ""),
                "last_updated": v.get("last_updated", ""),
                "message_count": v.get("message_count", 0),
            })
    return result


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
