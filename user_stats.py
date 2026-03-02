"""
Статистика и портреты пользователей: ранги по полит. взглядам, ежедневное обновление портрета.

Файл user_stats.json НЕ обнуляется при перезапуске бота — данные накапливаются.
Он заполняется, когда:
  - кто-то пишет боту (упоминание или ответ) — создаётся запись и портрет;
  - срабатывает анализ полит. контекста (после 5 полит. сообщений) — вызывается record_message.
Если файл пустой — такого рода активность ещё не происходила.
"""

import json
import logging
from datetime import date
from pathlib import Path

from ai_analyzer import update_user_portrait, assess_tone_toward_bot

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
USERS_JSON = DATA_DIR / "user_stats.json"

RANKS = ("loyal", "neutral", "opposition", "unknown")


def _load() -> dict:
    if not USERS_JSON.exists():
        return {"users": {}}
    try:
        data = json.loads(USERS_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "users" in data else {"users": {}}
    except Exception as e:
        logger.warning("Не удалось загрузить user_stats: %s", e)
        return {"users": {}}


def _save(data: dict) -> None:
    try:
        USERS_JSON.parent.mkdir(parents=True, exist_ok=True)
        USERS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.exception("Не удалось сохранить user_stats: %s", e)


def _default_user(user_id: int, display_name: str = "") -> dict:
    return {
        "user_id": user_id,
        "display_name": display_name or str(user_id),
        "rank": "unknown",
        "portrait": "",
        "portrait_updated_date": "",
        "stats": {
            "political_messages": 0,
            "positive_sentiment": 0,
            "negative_sentiment": 0,
            "neutral_sentiment": 0,
            "total_messages": 0,
            "warnings_received": 0,
        },
        "daily_buffer": [],
        "yesterday_quotes": [],
        "messages_to_bot_buffer": [],  # последние обращения к боту для оценки тона
        "tone_to_bot": "",  # личная оценка по настроению обращений к боту
    }


def get_user(user_id: int, display_name: str = "") -> dict:
    """Возвращает данные пользователя; создаёт запись при первом обращении."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
        _save(data)
    else:
        if display_name and not data["users"][key].get("display_name"):
            data["users"][key]["display_name"] = display_name
            _save(data)
    return data["users"][key].copy()


def _ensure_daily_buffer_clean(user_data: dict) -> None:
    """Оставить в daily_buffer только записи за сегодня."""
    today = date.today().isoformat()
    user_data["daily_buffer"] = [x for x in user_data.get("daily_buffer", []) if x.get("date") == today]


def record_message(user_id: int, text_snippet: str, sentiment: str, is_political: bool, display_name: str = "") -> None:
    """Учитывает сообщение: статистика и буфер дня для портрета."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
    u = data["users"][key]
    if display_name:
        u["display_name"] = display_name
    u["stats"]["total_messages"] = u["stats"].get("total_messages", 0) + 1
    if is_political:
        u["stats"]["political_messages"] = u["stats"].get("political_messages", 0) + 1
    if sentiment == "positive":
        u["stats"]["positive_sentiment"] = u["stats"].get("positive_sentiment", 0) + 1
    elif sentiment == "negative":
        u["stats"]["negative_sentiment"] = u["stats"].get("negative_sentiment", 0) + 1
    else:
        u["stats"]["neutral_sentiment"] = u["stats"].get("neutral_sentiment", 0) + 1
    u["daily_buffer"].append({
        "text": (text_snippet or "")[:500],
        "sentiment": sentiment,
        "date": date.today().isoformat(),
    })
    _save(data)


def record_warning(user_id: int) -> None:
    """Учитывает выданное пользователю замечание."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id)
    data["users"][key]["stats"]["warnings_received"] = data["users"][key]["stats"].get("warnings_received", 0) + 1
    _save(data)


def _compute_rank_from_stats(stats: dict) -> str:
    """Ранг по счётчикам тональности (только по полит. сообщениям)."""
    pos = stats.get("positive_sentiment", 0)
    neg = stats.get("negative_sentiment", 0)
    neu = stats.get("neutral_sentiment", 0)
    if pos + neg + neu == 0:
        return "unknown"
    if pos > neg and pos >= neu:
        return "loyal"
    if neg > pos and neg >= neu:
        return "opposition"
    return "neutral"


def daily_update_user(user_id: int) -> bool:
    """
    Обновляет портрет пользователя по буферу за день (вызов ИИ), сбрасывает буфер за сегодня.
    Возвращает True, если обновление прошло.
    """
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return False
    u = data["users"][key]
    today = date.today().isoformat()
    _ensure_daily_buffer_clean(u)
    daily_messages = u.get("daily_buffer", [])
    if not daily_messages and u.get("portrait_updated_date") == today:
        return True
    try:
        new_portrait, new_rank = update_user_portrait(
            u.get("portrait", ""),
            daily_messages,
            u.get("display_name", str(user_id)),
        )
        u["portrait"] = new_portrait
        u["rank"] = new_rank if new_rank in RANKS else _compute_rank_from_stats(u["stats"])
        u["portrait_updated_date"] = today
        # сохраняем последние фразы дня для редкой отсылки «а вчера ты сказал»
        u["yesterday_quotes"] = [m.get("text", "").strip()[:120] for m in daily_messages[-5:] if (m.get("text") or "").strip()]
        u["daily_buffer"] = []
        _save(data)
        logger.info("Портрет пользователя %s обновлён, ранг %s", user_id, u["rank"])
        return True
    except Exception as e:
        logger.exception("Ошибка обновления портрета %s: %s", user_id, e)
        u["rank"] = _compute_rank_from_stats(u["stats"])
        u["portrait_updated_date"] = today
        u["yesterday_quotes"] = [m.get("text", "").strip()[:120] for m in daily_messages[-5:] if (m.get("text") or "").strip()]
        u["daily_buffer"] = []
        _save(data)
        return False


def record_message_to_bot(user_id: int, text: str, display_name: str = "") -> None:
    """Учитывает обращение пользователя к боту (для оценки тона обращений)."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
    u = data["users"][key]
    if display_name:
        u["display_name"] = display_name
    buf = u.get("messages_to_bot_buffer") or []
    buf.append({"text": (text or "").strip()[:500], "date": date.today().isoformat()})
    u["messages_to_bot_buffer"] = buf[-20:]
    _save(data)


def _update_tone_to_bot(u: dict) -> None:
    """Обновляет tone_to_bot по буферу обращений к боту (вызов ИИ)."""
    buf = u.get("messages_to_bot_buffer") or []
    texts = [x.get("text", "").strip() for x in buf if (x.get("text") or "").strip()]
    if len(texts) < 2:
        return
    try:
        tone = assess_tone_toward_bot(texts)
        u["tone_to_bot"] = tone
        u["messages_to_bot_buffer"] = buf[-5:]
        return
    except Exception as e:
        logger.debug("Оценка тона к боту: %s", e)


def get_yesterday_quotes(user_id: int) -> list[str]:
    """Цитаты из недавних сообщений пользователя (для редкой отсылки «а вчера ты сказал»)."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return []
    return list(u.get("yesterday_quotes") or [])[:5]


def get_portrait_for_reply(user_id: int, display_name: str = "") -> str:
    """
    Возвращает актуальный портрет пользователя для ответа.
    Если портрет не обновлялся сегодня — перед возвратом запускает ежедневное обновление по буферу.
    """
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
        _save(data)
    u = data["users"][key]
    if display_name:
        u["display_name"] = display_name
        _save(data)
    today = date.today().isoformat()
    if u.get("portrait_updated_date") != today:
        daily_update_user(user_id)
        data = _load()
        u = data["users"][key]
    _update_tone_to_bot(u)
    _save(data)
    portrait = u.get("portrait", "")
    tone = (u.get("tone_to_bot") or "").strip()
    if tone:
        portrait = (portrait + "\n\nНастроение обращений к боту: " + tone).strip()
    return portrait


def get_stats_for_log() -> str:
    """Формирует текст статистики по всем пользователям для вывода в лог."""
    data = _load()
    users = data.get("users", {})
    if not users:
        return "База участников пуста."
    lines = ["=== Статистика пользователей ===", f"Всего: {len(users)}", ""]
    for uid, u in sorted(users.items(), key=lambda x: -x[1]["stats"].get("total_messages", 0)):
        name = u.get("display_name") or uid
        rank = u.get("rank", "unknown")
        s = u.get("stats", {})
        lines.append(f"id={uid} | {name} | ранг: {rank}")
        lines.append(f"  сообщений: {s.get('total_messages', 0)}, полит.: {s.get('political_messages', 0)} | "
                    f"+/−/0: {s.get('positive_sentiment', 0)}/{s.get('negative_sentiment', 0)}/{s.get('neutral_sentiment', 0)} | "
                    f"замечаний: {s.get('warnings_received', 0)}")
        portrait = (u.get("portrait") or "").strip()
        if portrait:
            lines.append(f"  портрет: {portrait[:150]}{'…' if len(portrait) > 150 else ''}")
        tone = (u.get("tone_to_bot") or "").strip()
        if tone:
            lines.append(f"  настроение к боту: {tone}")
        lines.append("")
    lines.append(f"Файл базы: {USERS_JSON}")
    return "\n".join(lines)


def get_ranks_for_chat() -> str:
    """Формирует текст рангов для вывода в чат (кратко, до ~4000 символов)."""
    from html import escape
    data = _load()
    users = data.get("users", {})
    if not users:
        return "База участников пуста."
    rank_emoji = {"loyal": "🇷🇺", "neutral": "⚪", "opposition": "🔴", "unknown": "❓"}
    lines = ["<b>Ранги участников</b> (по полит. взглядам):\n"]
    for uid, u in sorted(users.items(), key=lambda x: -x[1]["stats"].get("total_messages", 0)):
        name = escape(str(u.get("display_name") or uid))
        rank = u.get("rank", "unknown")
        em = rank_emoji.get(rank, "❓")
        s = u.get("stats", {})
        pol = s.get("political_messages", 0)
        warn = s.get("warnings_received", 0)
        tone = (u.get("tone_to_bot") or "").strip()
        tone_word = tone.split(",")[0].strip().split()[0] if tone else ""
        part = f"{em} {name} — {rank}"
        if tone_word:
            part += f" | к боту: {escape(tone_word)}"
        if pol or warn:
            part += f" (полит.: {pol}, замечаний: {warn})"
        lines.append(part)
    return "\n".join(lines)[:4000]
