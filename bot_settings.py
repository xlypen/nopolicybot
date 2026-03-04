"""
Настройки бота. Хранятся в bot_settings.json.
"""

import json
import time
from datetime import date
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "bot_settings.json"

DEFAULTS = {
    # Модерация политики
    "moderation_enabled": True,
    "analyze_images": True,
    "reactions_on_photos": True,
    "msgs_before_react": 5,
    "style_moderate_react": "praise",
    "style_active_frequency": "every_other",
    "style_beast_frequency": "every",
    "reset_after_neutral": 25,
    "patience_phrase_enabled": True,
    "article_line_enabled": True,
    "use_personalized_remarks": True,
    # Поощрения
    "encouragement_enabled": True,
    "encouragement_style": "both",
    # Реакции 1-5 (только эмодзи, разрешённые Telegram: https://core.telegram.org/bots/api#reactiontypeemoji)
    "reactions_political_1_5": True,
    "reactions_1_5_mode": "reaction_only",
    "reactions_1_5_positive_emoji": ["👍", "❤", "🥰", "🤩", "👏", "😁", "🔥", "🎉", "🤯"],
    "reactions_1_5_negative_emoji": ["👎", "🤮", "🤬", "💩", "😢"],
    "reactions_1_5_neutral_emoji": ["🤔", "🤯", "😱", "🤩"],
    # Спонтанные реакции
    "spontaneous_reactions": False,
    "spontaneous_max_per_day": 5,
    "spontaneous_min_interval_sec": 3600,
    "spontaneous_check_chance": 0.2,
    "spontaneous_emojis": ["👍", "❤", "🔥", "🥰"],
    # Вопрос дня
    "question_of_day": True,
    "question_of_day_start_hour": 20,
    "question_of_day_end_hour": 22,
    "question_of_day_min_interval_sec": 120,
    # Ответы в личку
    "reply_to_bot_enabled": True,
    "reply_kind_enabled": True,
    "reply_rude_enabled": True,
    "reply_technical_enabled": True,
    "reply_yesterday_quotes_chance": 0.01,
    "reply_fallback_on_error": "сейчас не в настроении, напиши потом.",
    # Приветствие и команды
    "greeting_on_join": True,
    "greeting_text": "Привет, котятки! Пришёл смотреть за вашим поведением.",
    "cmd_ranks_enabled": True,
    "cmd_stats_enabled": True,
    # Технические
    "api_min_interval_sec": 5,
    "batch_style_cache_sec": 300,
    "min_context_lines": 15,
    "min_context_lines_1_5": 5,
    # Переопределения по чатам
    "chat_settings": {},
}


def _load() -> dict:
    if not SETTINGS_PATH.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        merged = dict(DEFAULTS)
        for k, v in data.items():
            if k in DEFAULTS:
                merged[k] = v
        if "chat_settings" in data and isinstance(data["chat_settings"], dict):
            merged["chat_settings"] = data["chat_settings"]
        return merged
    except Exception:
        return dict(DEFAULTS)


def _save(data: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get(key: str, chat_id: int | None = None):
    data = _load()
    base = data.get(key, DEFAULTS.get(key))
    if chat_id is not None:
        overrides = data.get("chat_settings") or {}
        chat_key = f"chat_{chat_id}"
        if chat_key in overrides and key in overrides[chat_key]:
            return overrides[chat_key][key]
    return base


def get_all() -> dict:
    return _load()


def set_value(key: str, value) -> bool:
    if key not in DEFAULTS or key == "chat_settings":
        return False
    data = _load()
    data[key] = value
    _save(data)
    return True


def set_all(updates: dict) -> None:
    data = _load()
    for k, v in updates.items():
        if k in DEFAULTS and k != "chat_settings":
            data[k] = v
    _save(data)


def set_chat_override(chat_id: int, key: str, value) -> None:
    data = _load()
    if "chat_settings" not in data:
        data["chat_settings"] = {}
    ck = f"chat_{chat_id}"
    if ck not in data["chat_settings"]:
        data["chat_settings"][ck] = {}
    if key in DEFAULTS and key != "chat_settings":
        data["chat_settings"][ck][key] = value
        _save(data)


def clear_chat_override(chat_id: int, key: str | None = None) -> None:
    data = _load()
    overrides = data.get("chat_settings") or {}
    ck = f"chat_{chat_id}"
    if ck not in overrides:
        return
    if key:
        overrides[ck].pop(key, None)
    else:
        del overrides[ck]
    data["chat_settings"] = overrides
    _save(data)


def _clamp_int(val, lo: int, hi: int, default: int) -> int:
    try:
        n = int(val)
        return max(lo, min(hi, n))
    except (TypeError, ValueError):
        return default


def _clamp_float(val, lo: float, hi: float, default: float) -> float:
    try:
        n = float(val)
        return max(lo, min(hi, n))
    except (TypeError, ValueError):
        return default


def get_int(key: str, chat_id: int | None = None, lo: int = 0, hi: int = 9999) -> int:
    v = get(key, chat_id)
    d = DEFAULTS.get(key, 0)
    if isinstance(d, list):
        return _clamp_int(v, lo, hi, lo)
    return _clamp_int(v, lo, hi, int(d) if isinstance(d, (int, float)) else lo)


def get_float(key: str, chat_id: int | None = None, lo: float = 0, hi: float = 1) -> float:
    v = get(key, chat_id)
    d = DEFAULTS.get(key, 0)
    return _clamp_float(v, lo, hi, float(d) if isinstance(d, (int, float)) else lo)


def get_list(key: str, chat_id: int | None = None) -> list:
    v = get(key, chat_id)
    d = DEFAULTS.get(key, [])
    if isinstance(v, list) and v:
        return [str(x) for x in v]
    if isinstance(d, list):
        return list(d)
    return []


# Спонтанные реакции: счётчик (в памяти)
_spontaneous_count_today = 0
_spontaneous_date = ""
_spontaneous_last_at = 0.0


def can_spontaneous_reaction() -> bool:
    if not get("spontaneous_reactions"):
        return False
    global _spontaneous_count_today, _spontaneous_date, _spontaneous_last_at
    today = date.today().isoformat()
    if _spontaneous_date != today:
        _spontaneous_count_today = 0
        _spontaneous_date = today
    max_per_day = get_int("spontaneous_max_per_day", lo=1, hi=20)
    if _spontaneous_count_today >= max_per_day:
        return False
    interval = get_int("spontaneous_min_interval_sec", lo=300, hi=7200)
    if time.time() - _spontaneous_last_at < interval:
        return False
    return True


def mark_spontaneous_reaction() -> None:
    global _spontaneous_count_today, _spontaneous_last_at
    _spontaneous_count_today += 1
    _spontaneous_last_at = time.time()
