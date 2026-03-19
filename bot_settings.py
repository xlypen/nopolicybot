"""
Настройки бота. Хранятся в bot_settings.json.
"""

import json
import logging

logger = logging.getLogger(__name__)
import time
import tempfile
from datetime import date
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "bot_settings.json"
DB_PATH = Path(__file__).resolve().parent / "data" / "bot.db"

DEFAULTS = {
    # Модерация политики
    "moderation_enabled": True,
    "analyze_images": True,
    "analyze_voice": True,
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
    "qod_graph_mode_enabled": False,
    # Ответы в личку
    "reply_to_bot_enabled": True,
    "reply_kind_enabled": True,
    "reply_rude_enabled": True,
    "reply_technical_enabled": True,
    "reply_pause_on_reject_enabled": True,
    "reply_pause_sec": 180,
    "reply_pause_text": "пошел нахуй.",
    "reply_resume_on_apology_enabled": True,
    "reply_resume_text": "ладно, амнистия. но не путай это со слабостью.",
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
    "ai_fast_cache_ttl_sec": 45,
    "ai_fast_cache_max_items": 512,
    "ai_parallel_reply_enabled": True,
    "social_graph_realtime_enabled": True,
    "social_graph_realtime_interval_sec": 90,
    "social_graph_realtime_min_new_messages": 1,
    "social_graph_advanced_insights_enabled": False,
    "social_graph_ranked_layout_enabled": False,
    "social_graph_conflict_forecast_enabled": False,
    "social_graph_roles_enabled": False,
    "chat_topic_recommender_enabled": False,
    "bot_explainability_enabled": False,
    "content_digest_enabled": False,
    "content_digest_send_enabled": False,
    "content_digest_interval_hours": 24,
    "content_digest_chat_id": 0,
    "primary_moderation_topic": "politics",
    # Retention / growth automation
    "retention_auto_dm_enabled": False,
    "retention_auto_dm_interval_sec": 3600,
    "retention_auto_dm_limit_per_run": 3,
    "retention_auto_dm_min_churn_risk": 0.72,
    "retention_auto_dm_cooldown_hours": 24,
    "retention_auto_dm_text": "Мы давно тебя не видели. Заходи в чат, нам важна твоя точка зрения.",
    "churn_detection_enabled": True,
    "churn_check_interval_sec": 3600,
    # Факт-чек
    "factcheck_enabled": True,
    "factcheck_min_interval_sec": 300,
    "factcheck_max_text_len": 500,
    # Переопределения по чатам
    "chat_settings": {},
    "moderation_force_style": "",  # "beast"|"active" — принудительный стиль, игнорировать moderate
}


def _load() -> dict:
    from services.storage_cutover import storage_db_reads_enabled, storage_json_fallback_enabled
    from services.sqlite_storage import get_storage

    merged = dict(DEFAULTS)

    if storage_db_reads_enabled():
        st = get_storage()
        if st:
            data = st.get_global_settings()
            if data:
                for k, v in data.items():
                    if k in DEFAULTS and k != "chat_settings":
                        merged[k] = v
                return merged

    if storage_json_fallback_enabled() and SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in DEFAULTS:
                        merged[k] = v
                if "chat_settings" in data and isinstance(data["chat_settings"], dict):
                    merged["chat_settings"] = data["chat_settings"]
        except Exception:
            pass
    return merged


def _save(data: dict) -> None:
    from services.storage_cutover import storage_db_writes_enabled, storage_json_writes_enabled
    from services.sqlite_storage import get_storage

    to_save = {k: v for k, v in data.items() if k in DEFAULTS and k != "chat_settings"}

    if storage_db_writes_enabled():
        st = get_storage()
        if st:
            st.set_global_settings(to_save)

    if storage_json_writes_enabled():
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(data, ensure_ascii=False, indent=2)
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=SETTINGS_PATH.parent) as tmp:
                tmp.write(payload)
                tmp_path = Path(tmp.name)
            tmp_path.replace(SETTINGS_PATH)
        except Exception:
            pass


def _get_chat_overrides_from_db(chat_id: int) -> dict:
    """Переопределения настроек чата из таблицы chat_settings (БД)."""
    if not DB_PATH.exists():
        return {}
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT settings FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        conn.close()
        if not row:
            return {}
        raw = row[0]
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception as e:
        logger.debug("_get_chat_overrides_from_db: %s", e)
        return {}


def get(key: str, chat_id: int | None = None):
    data = _load()
    base = data.get(key, DEFAULTS.get(key))
    if chat_id is not None:
        overrides = _get_chat_overrides_from_db(chat_id)
        if not overrides and data.get("chat_settings"):
            overrides = (data.get("chat_settings") or {}).get(f"chat_{chat_id}", {})
        if key in overrides:
            return overrides[key]
    return base


def get_all() -> dict:
    return _load()


def set_all(updates: dict) -> None:
    data = _load()
    for k, v in updates.items():
        if k in DEFAULTS and k != "chat_settings":
            data[k] = v
    _save(data)


def _set_chat_overrides_in_db(chat_id: int, overrides: dict) -> None:
    """Записать переопределения чата в таблицу chat_settings (БД). Пустой dict — удалить строку."""
    if not DB_PATH.exists():
        return
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        if not overrides:
            conn.execute("DELETE FROM chat_settings WHERE chat_id = ?", (chat_id,))
        else:
            payload = json.dumps(overrides, ensure_ascii=False)
            conn.execute(
                "INSERT INTO chat_settings (chat_id, settings) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET settings = excluded.settings",
                (chat_id, payload),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("_set_chat_overrides_in_db: %s", e)


def set_chat_override(chat_id: int, key: str, value) -> None:
    """Устанавливает переопределение настройки для конкретного чата. Пишет в БД и при возможности в JSON."""
    if key not in DEFAULTS or key == "chat_settings":
        return
    overrides = dict(get_chat_overrides(chat_id))
    overrides[key] = value
    _set_chat_overrides_in_db(chat_id, overrides)
    data = _load()
    ckey = f"chat_{chat_id}"
    data.setdefault("chat_settings", {})[ckey] = overrides
    _save(data)


def clear_chat_overrides(chat_id: int, key: str | None = None) -> None:
    """Сбрасывает переопределения для чата. key=None — сбросить все. Обновляет БД и JSON."""
    overrides = dict(get_chat_overrides(chat_id))
    if not overrides:
        return
    if key is None:
        overrides = {}
    else:
        overrides.pop(key, None)
    _set_chat_overrides_in_db(chat_id, overrides)
    data = _load()
    ckey = f"chat_{chat_id}"
    if ckey not in (data.get("chat_settings") or {}):
        return
    if key is None:
        (data.setdefault("chat_settings", {})).pop(ckey, None)
    else:
        (data["chat_settings"][ckey]).pop(key, None)
        if not data["chat_settings"][ckey]:
            del data["chat_settings"][ckey]
    _save(data)


def get_chat_overrides(chat_id: int) -> dict:
    """Возвращает все переопределения для чата. Сначала БД (chat_settings), потом JSON."""
    out = _get_chat_overrides_from_db(chat_id)
    if out:
        return dict(out)
    data = _load()
    overrides = data.get("chat_settings") or {}
    return dict(overrides.get(f"chat_{chat_id}", {}))


CHAT_MODE_PRESETS = {
    "default": {"_label": "По умолчанию", "_desc": "Глобальные настройки"},
    "soft": {
        "_label": "Мягкий",
        "_desc": "Реакции 1–5, замечания с 5-го, через раз",
        "msgs_before_react": 5,
        "reactions_political_1_5": True,
        "reactions_1_5_mode": "reaction_only",
        "style_active_frequency": "every_other",
        "style_beast_frequency": "every_other",
        "min_context_lines": 5,
        "min_context_lines_1_5": 1,
    },
    "active": {
        "_label": "Активный",
        "_desc": "Реакции с 1-го, замечания с 3-го, каждое",
        "msgs_before_react": 3,
        "reactions_political_1_5": True,
        "reactions_1_5_mode": "random",
        "style_active_frequency": "every",
        "style_beast_frequency": "every",
        "moderation_force_style": "active",
        "min_context_lines": 5,
        "min_context_lines_1_5": 1,
    },
    "beast": {
        "_label": "Зверь",
        "_desc": "Реакции и замечания с 1-го полит. сообщения",
        "msgs_before_react": 1,
        "reactions_political_1_5": True,
        "reactions_1_5_mode": "random",
        "style_active_frequency": "every",
        "style_beast_frequency": "every",
        "moderation_force_style": "beast",
        "min_context_lines": 1,
        "min_context_lines_1_5": 1,
    },
}


def set_chat_mode(chat_id: int, mode: str) -> bool:
    """Применяет пресет режима для чата. mode: default|soft|active|beast."""
    label = CHAT_MODE_PRESETS.get(mode, {}).get("_label", mode)
    if mode == "default":
        clear_chat_overrides(chat_id)
        logger.info("[чат %s] Режим сброшен на «по умолчанию»", chat_id)
        return True
    preset = CHAT_MODE_PRESETS.get(mode)
    if not preset:
        return False
    clear_chat_overrides(chat_id)
    for k, v in preset.items():
        if k.startswith("_"):
            continue
        set_chat_override(chat_id, k, v)
    logger.info("[чат %s] Режим переключён на «%s»", chat_id, label)
    return True


def get_chat_mode(chat_id: int) -> str:
    """Определяет текущий режим чата по переопределениям. Возвращает default|soft|active|beast."""
    overrides = get_chat_overrides(chat_id)
    if not overrides:
        return "default"
    for mode, preset in CHAT_MODE_PRESETS.items():
        if mode == "default":
            continue
        match = True
        for k, v in preset.items():
            if k.startswith("_"):
                continue
            if overrides.get(k) != v:
                match = False
                break
        if match:
            return mode
    return "custom"


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
