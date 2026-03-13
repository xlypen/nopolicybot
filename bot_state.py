"""
Сохранение и восстановление состояния бота при резком отключении.
Состояние записывается в bot_state.json при изменениях и загружается при старте.
"""

import logging
import time
from pathlib import Path

from utils.json_store import atomic_save, safe_load

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parent / "bot_state.json"
_SAVE_DEBOUNCE_LAST = 0.0
_SAVE_DEBOUNCE_SEC = 2.0


def load_state() -> dict:
    """Загружает состояние из файла. Возвращает dict с ключами chats, dm_silence."""
    data = safe_load(STATE_PATH, default={})
    if not isinstance(data, dict):
        return {}
    return data


def save_state(
    chat_political_count: dict,
    chat_warning_count: dict,
    chat_messages_since_political: dict,
    chat_first_remark_done: dict,
    chat_last_praise_date: dict,
    dm_silence_until: dict,
) -> None:
    """Сохраняет состояние в файл. dm_silence: user_id -> end_monotonic; конвертируем в end_unix."""
    global _SAVE_DEBOUNCE_LAST
    now = time.time()
    if now - _SAVE_DEBOUNCE_LAST < _SAVE_DEBOUNCE_SEC:
        return
    _SAVE_DEBOUNCE_LAST = now

    now_mono = time.monotonic()
    dm_silence_persist = {}
    for uid, end_mono in dm_silence_until.items():
        remaining = end_mono - now_mono
        if remaining > 0:
            dm_silence_persist[str(uid)] = round(now + remaining, 1)

    payload = {
        "chats": {
            str(cid): {
                "political_count": chat_political_count.get(cid, 0),
                "warning_count": chat_warning_count.get(cid, 0),
                "messages_since_political": chat_messages_since_political.get(cid, 0),
                "first_remark_done": chat_first_remark_done.get(cid, False),
                "last_praise_date": chat_last_praise_date.get(cid, ""),
            }
            for cid in set(
                list(chat_political_count.keys())
                + list(chat_warning_count.keys())
                + list(chat_messages_since_political.keys())
                + list(chat_first_remark_done.keys())
                + list(chat_last_praise_date.keys())
            )
        },
        "dm_silence": dm_silence_persist,
    }
    try:
        atomic_save(STATE_PATH, payload)
    except Exception as e:
        logger.warning("Ошибка сохранения состояния бота: %s", e)


def apply_state(
    chat_political_count: dict,
    chat_warning_count: dict,
    chat_messages_since_political: dict,
    chat_first_remark_done: dict,
    chat_last_praise_date: dict,
    dm_silence_until: dict,
) -> None:
    """Применяет загруженное состояние к in-memory dict'ам."""
    data = load_state()
    chats = data.get("chats") or {}
    for cid_str, c in chats.items():
        try:
            cid = int(cid_str)
        except ValueError:
            continue
        if c.get("political_count", 0) > 0:
            chat_political_count[cid] = int(c.get("political_count", 0))
        if c.get("warning_count", 0) > 0:
            chat_warning_count[cid] = int(c.get("warning_count", 0))
        if c.get("messages_since_political", 0) > 0:
            chat_messages_since_political[cid] = int(c.get("messages_since_political", 0))
        if c.get("first_remark_done"):
            chat_first_remark_done[cid] = True
        if c.get("last_praise_date"):
            chat_last_praise_date[cid] = str(c.get("last_praise_date", ""))
    now = time.time()
    now_mono = time.monotonic()
    for uid_str, end_unix in (data.get("dm_silence") or {}).items():
        try:
            uid = int(uid_str)
        except ValueError:
            continue
        remaining = float(end_unix) - now
        if remaining > 0:
            dm_silence_until[uid] = now_mono + remaining
    if chats or data.get("dm_silence"):
        logger.info("Восстановлено состояние: %s чатов, %s пользователей в паузе", len(chats), len(data.get("dm_silence") or {}))
