"""
Статистика и портреты пользователей: ранги по полит. взглядам, ежедневное обновление портрета.
Хранит архив сообщений участников (до 1000 на человека) для построения глубокого портрета.

Файл user_stats.json НЕ обнуляется при перезапуске бота — данные накапливаются.
"""

import json
import logging
import tempfile
from datetime import date
from pathlib import Path

from ai_analyzer import update_user_portrait, assess_tone_toward_bot

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
USERS_JSON = DATA_DIR / "user_stats.json"
DB_PATH = DATA_DIR / "data" / "bot.db"
MESSAGES_ARCHIVE_LIMIT = 1000
IMAGES_ARCHIVE_LIMIT = 100
CLOSE_ATTENTION_VIEWS_LIMIT = 200

RANKS = ("loyal", "neutral", "opposition", "unknown")


def _migrate_question_of_day(data: dict) -> bool:
    """Добавляет question_of_day_* для старых записей."""
    modified = False
    for u in data.get("users", {}).values():
        if "question_of_day_enabled" not in u:
            u["question_of_day_enabled"] = False
            modified = True
        if "question_of_day_last_asked" not in u:
            u["question_of_day_last_asked"] = ""
            modified = True
        if "question_of_day_destination" not in u:
            u["question_of_day_destination"] = "chat"
            modified = True
    return modified


def _migrate_images_archive(data: dict) -> bool:
    """Добавляет images_archive для старых записей. Дополняет старые записи reaction_emoji, is_political."""
    modified = False
    for u in data.get("users", {}).values():
        if "images_archive" not in u:
            u["images_archive"] = []
            modified = True
        else:
            for img in u.get("images_archive") or []:
                if isinstance(img, dict):
                    if "reaction_emoji" not in img:
                        img["reaction_emoji"] = ""
                        modified = True
                    if "is_political" not in img:
                        img["is_political"] = False
                        modified = True
    return modified


def _migrate_close_attention(data: dict) -> bool:
    """Добавляет close_attention_enabled и close_attention_views для старых записей."""
    modified = False
    for u in data.get("users", {}).values():
        if "close_attention_enabled" not in u:
            u["close_attention_enabled"] = False
            modified = True
        if "close_attention_views" not in u:
            u["close_attention_views"] = []
            modified = True
    return modified


def _migrate_factcheck(data: dict) -> bool:
    """Добавляет factcheck_enabled для старых записей."""
    modified = False
    for u in data.get("users", {}).values():
        if "factcheck_enabled" not in u:
            u["factcheck_enabled"] = False
            modified = True
    return modified


def _migrate_portrait_image(data: dict) -> bool:
    """Добавляет portrait_image_updated_date для старых записей."""
    modified = False
    for u in data.get("users", {}).values():
        if "portrait_image_updated_date" not in u:
            u["portrait_image_updated_date"] = ""
            modified = True
    return modified


def _load() -> dict:
    if not USERS_JSON.exists():
        return {"users": {}, "chats": {}}
    try:
        data = json.loads(USERS_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"users": {}, "chats": {}}
        if "users" not in data:
            data["users"] = {}
        if "chats" not in data:
            data["chats"] = {}
        if _apply_migrations(data):
            _save(data)
        return data
    except Exception as e:
        logger.warning("Не удалось загрузить user_stats: %s", e)
        return {"users": {}, "chats": {}}


def _save(data: dict) -> None:
    from services.storage_cutover import storage_json_writes_enabled
    if not storage_json_writes_enabled():
        return
    try:
        USERS_JSON.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=USERS_JSON.parent) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        tmp_path.replace(USERS_JSON)
    except Exception as e:
        logger.exception("Не удалось сохранить user_stats: %s", e)


def _apply_migrations(data: dict) -> bool:
    """Единая точка миграций схемы user_stats.json."""
    modified = False
    modified = _migrate_question_of_day(data) or modified
    modified = _migrate_images_archive(data) or modified
    modified = _migrate_close_attention(data) or modified
    modified = _migrate_factcheck(data) or modified
    modified = _migrate_portrait_image(data) or modified
    return modified


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
        "messages_to_bot_buffer": [],
        "tone_to_bot": "",
        "tone_override": "",
        "tone_history": [],
        "messages_archive": [],  # deprecated, мигрируется в messages_by_chat
        "messages_by_chat": {},  # chat_id -> [{text, date}, ...], до 1000 на чат
        "question_of_day_enabled": False,  # задавать ли боту «вопрос дня» вечером
        "question_of_day_last_asked": "",  # дата последнего вопроса "YYYY-MM-DD"
        "question_of_day_destination": "chat",  # "chat" — в чат, "private" — в личку
        "images_archive": [],  # [{category, description, date, reaction_emoji, is_political}, ...], до IMAGES_ARCHIVE_LIMIT
        "close_attention_enabled": False,  # режим «пристальное внимание»: глубокий анализ, накопление взглядов, требование доказательств
        "close_attention_views": [],  # [{date, source, views, needs_evidence, evidence_found}, ...], до 200 записей
        "factcheck_enabled": False,  # факт-чек высказываний для этого пользователя
        "portrait_image_updated_date": "",  # дата последней генерации картинки портрета "YYYY-MM-DD"
    }


def _get_user_from_db(user_id: int) -> dict | None:
    """Данные пользователя из БД: display_name (users), total_messages (messages)."""
    if not DB_PATH.exists():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT first_name, username, last_name FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        cnt = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        conn.close()
        result = {}
        if row:
            name = (row[0] or row[1] or row[2] or "").strip()
            result["display_name"] = name or str(user_id)
        if int(cnt) > 0:
            result["stats"] = {"total_messages": int(cnt)}
        return result if result else None
    except Exception as e:
        logger.debug("_get_user_from_db: %s", e)
        return None


def get_user(user_id: int, display_name: str = "") -> dict:
    """Возвращает данные пользователя. БД (имя, счётчик сообщений) + JSON (ранг, полит, предупреждения)."""
    key = str(user_id)
    from_db = _get_user_from_db(user_id)
    data = _load()
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
        _save(data)
    else:
        if display_name and not data["users"][key].get("display_name"):
            data["users"][key]["display_name"] = display_name
            _save(data)
    out = data["users"][key].copy()
    if from_db:
        if from_db.get("display_name"):
            out["display_name"] = from_db["display_name"]
        if from_db.get("stats", {}).get("total_messages") is not None:
            out.setdefault("stats", {})["total_messages"] = from_db["stats"]["total_messages"]
    return out


def _ensure_messages_by_chat(u: dict, data: dict | None = None) -> bool:
    """Мигрирует messages_archive в messages_by_chat при первом обращении. Возвращает True если была миграция (нужно сохранить)."""
    if u.get("messages_by_chat"):
        return False
    by_chat = u.get("messages_by_chat") or {}
    old = u.get("messages_archive") or []
    if not old and not by_chat:
        u["messages_by_chat"] = {}
        u.pop("messages_archive", None)
        return True
    for m in old:
        cid = m.get("chat_id")
        ckey = str(cid) if cid is not None else "unknown"
        if ckey not in by_chat:
            by_chat[ckey] = []
        by_chat[ckey].append({"text": m.get("text", ""), "date": m.get("date", "")})
    for ckey in by_chat:
        by_chat[ckey] = by_chat[ckey][-MESSAGES_ARCHIVE_LIMIT:]
    u["messages_by_chat"] = by_chat
    u.pop("messages_archive", None)
    return True


def record_chat_message(
    user_id: int,
    text: str,
    display_name: str = "",
    chat_id: int | None = None,
    chat_title: str = "",
) -> None:
    """Сохраняет сообщение пользователя в архив (по чатам) и обновляет total_messages."""
    if not text or not (text := text.strip()):
        return
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
    u = data["users"][key]
    if _ensure_messages_by_chat(u):
        _save(data)
    if display_name:
        u["display_name"] = display_name
    by_chat = u["messages_by_chat"]
    # Нормализуем ключ чата: всегда строка для консистентности (int -5192849857 -> "-5192849857")
    ckey = str(int(chat_id)) if chat_id is not None else "unknown"
    if ckey not in by_chat:
        by_chat[ckey] = []
    today_str = date.today().isoformat()
    msg = {"text": text[:500], "date": today_str}
    last = by_chat[ckey][-1] if by_chat[ckey] else None
    if last and last.get("text") == msg["text"] and last.get("date") == today_str:
        return
    u["stats"]["total_messages"] = u["stats"].get("total_messages", 0) + 1
    by_chat[ckey].append(msg)
    by_chat[ckey] = by_chat[ckey][-MESSAGES_ARCHIVE_LIMIT:]
    if chat_id is not None:
        ckey = str(int(chat_id))
        if ckey not in data["chats"]:
            data["chats"][ckey] = {"title": chat_title or ckey, "last_seen": date.today().isoformat()}
        else:
            if chat_title:
                data["chats"][ckey]["title"] = chat_title
            data["chats"][ckey]["last_seen"] = date.today().isoformat()
    _save(data)


def record_image_analysis(
    user_id: int,
    category: str,
    description: str,
    display_name: str = "",
    reaction_emoji: str = "",
    is_political: bool = False,
) -> None:
    """Сохраняет результат анализа изображения: категория, описание, реакция бота, политичность."""
    if not category and not description:
        return
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
    u = data["users"][key]
    if display_name:
        u["display_name"] = display_name
    archive = u.get("images_archive") or []
    today = date.today().isoformat()
    archive.append({
        "category": category[:50],
        "description": (description or "")[:400],
        "date": today,
        "reaction_emoji": (reaction_emoji or "")[:10],
        "is_political": bool(is_political),
    })
    u["images_archive"] = archive[-IMAGES_ARCHIVE_LIMIT:]
    _save(data)


def get_user_images_archive(user_id: int) -> list[dict]:
    """Возвращает архив проанализированных изображений пользователя: [{category, description, date, reaction_emoji, is_political}, ...]."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return []
    return list(u.get("images_archive") or [])


def format_images_archive_for_context(user_id: int, max_items: int = 15) -> str:
    """
    Форматирует архив изображений для контекста ответа бота.
    Последние изображения (свежие в конце). Для объяснения «что на картинке», «почему такая реакция».
    """
    archive = get_user_images_archive(user_id)
    if not archive:
        return ""
    items = list(reversed(archive[-max_items:]))  # последние сначала
    lines = []
    for i, img in enumerate(items, 1):
        cat = img.get("category", "")
        desc = img.get("description", "")
        dt = img.get("date", "")
        emoji = img.get("reaction_emoji", "")
        pol = img.get("is_political", False)
        parts = [f"[{dt}] категория: {cat}"]
        if desc:
            parts.append(f"описание: {desc}")
        if emoji:
            parts.append(f"поставил реакцию {emoji}")
        if pol:
            parts.append("(политика)")
        lines.append(f"  {i}. {'; '.join(parts)}")
    return "Твои действия с изображениями пользователя (последние сначала):\n" + "\n".join(lines)


def clear_user_images_archive(user_id: int) -> bool:
    """Очищает архив проанализированных изображений пользователя."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return False
    data["users"][key]["images_archive"] = []
    _save(data)
    return True


def _get_display_names_from_db() -> dict[str, str]:
    """Читает имена из таблицы users (БД). {user_id: display_name}."""
    if not DB_PATH.exists():
        return {}
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT id, first_name, username, last_name FROM users"
        ).fetchall()
        conn.close()
        result = {}
        for uid, first, username, last in rows:
            name = (first or username or last or "").strip() or str(uid)
            result[str(uid)] = name
        return result
    except Exception as e:
        logger.debug("_get_display_names_from_db: %s", e)
        return {}


def get_user_display_names() -> dict[str, str]:
    """Возвращает {user_id: display_name}. Сначала БД (users), потом JSON."""
    names = _get_display_names_from_db()
    data = _load()
    for uid, u in (data.get("users") or {}).items():
        if uid not in names:
            names[uid] = (u.get("display_name") or uid)
    return names


def get_chats() -> list[dict]:
    """Возвращает список чатов, в которых бот видел сообщения. [{chat_id, title}, ...] по last_seen."""
    data = _load()
    chats = data.get("chats") or {}
    result = [{"chat_id": cid, "title": c.get("title") or cid} for cid, c in chats.items()]
    return sorted(result, key=lambda x: x["chat_id"])


def _get_users_in_chat_from_db(chat_id: int) -> list[str]:
    """Участники чата из таблицы messages (БД)."""
    if not DB_PATH.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM messages WHERE chat_id = ? AND user_id IS NOT NULL",
            (chat_id,),
        ).fetchall()
        conn.close()
        return [str(r[0]) for r in rows]
    except Exception as e:
        logger.debug("_get_users_in_chat_from_db: %s", e)
        return []


def get_users_in_chat(chat_id: int) -> list[str]:
    """Возвращает user_id (str) участников. Сначала БД (messages), потом JSON."""
    result = _get_users_in_chat_from_db(chat_id)
    if result:
        return result
    data = _load()
    cid_str = str(chat_id)
    for uid, u in data.get("users", {}).items():
        if _ensure_messages_by_chat(u):
            _save(data)
        by_chat = u.get("messages_by_chat") or {}
        if cid_str in by_chat and by_chat[cid_str]:
            result.append(uid)
    return result


def get_user_messages_archive(user_id: int, chat_id: int | None = None) -> list[dict]:
    """Возвращает архив сообщений. Если chat_id задан — только из этого чата. Формат: [{text, date}] или [{text, date, chat_id}] при объединении."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return []
    if _ensure_messages_by_chat(u):
        _save(data)
    by_chat = u.get("messages_by_chat") or {}
    if chat_id is not None:
        cid_str = str(chat_id)
        return list(by_chat.get(cid_str, []))
    result = []
    for cid, msgs in by_chat.items():
        for m in msgs:
            msg = dict(m)
            if cid != "unknown":
                msg["chat_id"] = int(cid) if cid.isdigit() or (cid.startswith("-") and cid[1:].isdigit()) else cid
            result.append(msg)
    result.sort(key=lambda x: x.get("date", ""))
    return result


def get_user_archive_by_chat(user_id: int) -> dict:
    """Возвращает архив по чатам: {chat_id: [{text, date}, ...], ...}."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return {}
    if _ensure_messages_by_chat(u):
        _save(data)
    return dict(u.get("messages_by_chat") or {})


def clear_user_archive(user_id: int, chat_id: int | str | None = None) -> bool:
    """Очищает архив пользователя. chat_id=None — весь архив, иначе только указанный чат."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return False
    u = data["users"][key]
    if _ensure_messages_by_chat(u):
        _save(data)
    by_chat = u["messages_by_chat"]
    if chat_id is None:
        u["messages_by_chat"] = {}
    else:
        ckey = str(chat_id)
        if ckey in by_chat:
            del by_chat[ckey]
    _save(data)
    return True


def get_user_messages_for_today(user_id: int) -> list[dict]:
    """Сообщения пользователя за сегодня (архив + daily_buffer) для «вопроса дня»."""
    today_str = date.today().isoformat()
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return []
    if _ensure_messages_by_chat(u):
        _save(data)
    result = []
    for msgs in (u.get("messages_by_chat") or {}).values():
        for m in msgs:
            if m.get("date", "").startswith(today_str):
                result.append({"text": m.get("text", ""), "date": m.get("date", ""), "sentiment": ""})
    for m in u.get("daily_buffer") or []:
        if m.get("date") == today_str:
            result.append({
                "text": m.get("text", ""),
                "date": m.get("date", ""),
                "sentiment": m.get("sentiment", ""),
            })
    result.sort(key=lambda x: x.get("date", ""))
    return result


def set_question_of_day_enabled(user_id: int, enabled: bool) -> bool:
    """Включить/выключить «вопрос дня» для пользователя."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return False
    data["users"][key]["question_of_day_enabled"] = bool(enabled)
    _save(data)
    return True


def set_question_of_day_destination(user_id: int, destination: str) -> bool:
    """Куда отправлять «вопрос дня»: "chat" — в чат, "private" — в личку."""
    if destination not in ("chat", "private"):
        return False
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return False
    data["users"][key]["question_of_day_destination"] = destination
    _save(data)
    return True


def get_chat_for_question_of_day(user_id: int) -> int | None:
    """Чат, куда отправить «вопрос дня»: где пользователь был активнее всего сегодня. None если нет сообщений за сегодня."""
    today_str = date.today().isoformat()
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return None
    if _ensure_messages_by_chat(u):
        _save(data)
    by_chat = u.get("messages_by_chat") or {}
    best_chat, best_count = None, 0
    for ckey, msgs in by_chat.items():
        if ckey == "unknown":
            continue
        count = sum(1 for m in msgs if (m.get("date") or "").startswith(today_str))
        if count > best_count:
            best_count = count
            try:
                best_chat = int(ckey)
            except ValueError:
                pass
    return best_chat


def get_user_chats_for_question_of_day(user_id: int) -> list[dict]:
    """Чаты пользователя для выбора «вопрос дня»: где есть сообщения. [{chat_id, title, today_count}, ...], отсортировано по активности сегодня."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return []
    if _ensure_messages_by_chat(u):
        _save(data)
    by_chat = u.get("messages_by_chat") or {}
    chats = data.get("chats") or {}
    today_str = date.today().isoformat()
    result = []
    for ckey, msgs in by_chat.items():
        if ckey == "unknown":
            continue
        try:
            cid = int(ckey)
        except ValueError:
            continue
        today_count = sum(1 for m in msgs if (m.get("date") or "").startswith(today_str))
        title = (chats.get(ckey) or {}).get("title") or ckey
        result.append({"chat_id": cid, "title": str(title), "today_count": today_count})
    result.sort(key=lambda x: (-x["today_count"], x["chat_id"]))
    return result


def get_users_for_question_of_day() -> list[tuple[int, str]]:
    """Пользователи с включённым «вопрос дня», которым ещё не задали сегодня. Возвращает [(user_id, display_name), ...]."""
    today_str = date.today().isoformat()
    data = _load()
    result = []
    for uid, u in data.get("users", {}).items():
        if not u.get("question_of_day_enabled"):
            continue
        if u.get("question_of_day_last_asked") == today_str:
            continue
        if _ensure_messages_by_chat(u):
            _save(data)
        result.append((int(uid), u.get("display_name") or uid))
    return result


def mark_question_of_day_asked(user_id: int) -> None:
    """Отметить, что пользователю задали вопрос дня сегодня."""
    data = _load()
    key = str(user_id)
    if key in data["users"]:
        data["users"][key]["question_of_day_last_asked"] = date.today().isoformat()
        _save(data)


def record_message(user_id: int, text_snippet: str, sentiment: str, is_political: bool, display_name: str = "") -> None:
    """Учитывает полит. сообщение: political_messages, sentiment, daily_buffer. total_messages ведётся в record_chat_message."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
    u = data["users"][key]
    if display_name:
        u["display_name"] = display_name
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


def _ensure_daily_buffer_clean(user_data: dict) -> None:
    """Оставить в daily_buffer только записи за сегодня."""
    today = date.today().isoformat()
    user_data["daily_buffer"] = [x for x in user_data.get("daily_buffer", []) if x.get("date") == today]


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
    except Exception as e:
        logger.debug("Оценка тона к боту: %s", e)


def get_yesterday_quotes(user_id: int) -> list[str]:
    """Цитаты из недавних сообщений пользователя (для редкой отсылки «а вчера ты сказал»)."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return []
    return list(u.get("yesterday_quotes") or [])[:5]


def get_portrait_for_reply_fast(user_id: int, display_name: str = "") -> str:
    """
    Возвращает портрет без вызова ИИ (daily_update, tone_update).
    Для быстрого ответа — обновления можно запустить в фоне отдельно.
    """
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return ""
    u = data["users"][key]
    if _ensure_messages_by_chat(u):
        _save(data)
    portrait = (u.get("portrait") or "").strip()
    tone = (u.get("tone_override") or "").strip() or (u.get("tone_to_bot") or "").strip()
    if tone:
        portrait = (portrait + "\n\nНастроение обращений к боту: " + tone).strip()
    return portrait


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
    tone = (u.get("tone_override") or "").strip() or (u.get("tone_to_bot") or "").strip()
    if tone:
        portrait = (portrait + "\n\nНастроение обращений к боту: " + tone).strip()
    return portrait


def set_deep_portrait(user_id: int, portrait: str, rank: str = "neutral") -> bool:
    """Сохраняет глубокий портрет пользователя (из архива сообщений + ИИ)."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id)
    u = data["users"][key]
    u["portrait"] = (portrait or "").strip()[:8000]
    u["rank"] = rank if rank in RANKS else "neutral"
    u["portrait_updated_date"] = date.today().isoformat()
    _save(data)
    return True


def set_portrait_image_updated_date(user_id: int) -> bool:
    """Обновляет дату последней генерации картинки портрета."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return False
    data["users"][key]["portrait_image_updated_date"] = date.today().isoformat()
    _save(data)
    return True


def save_tone_override(
    user_id: int,
    value: str | None,
    add_to_history: bool = False,
    save_current_to_history: bool = False,
) -> bool:
    """Сохраняет ручное настроение. value=None — сброс на авто. add_to_history — добавить value в историю. save_current_to_history — перед сбросом сохранить текущее в историю."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        return False
    u = data["users"][key]
    cur = (u.get("tone_override") or "").strip()
    new_val = (value or "").strip()
    save_prev = (save_current_to_history or (add_to_history and cur != new_val)) and cur
    if save_prev:
        hist = u.get("tone_history") or []
        hist = [cur] + [x for x in hist if x != cur][:2]
        u["tone_history"] = hist[:3]
    u["tone_override"] = new_val
    if add_to_history and new_val:
        hist = u.get("tone_history") or []
        hist = [new_val] + [x for x in hist if x != new_val][:2]
        u["tone_history"] = hist[:3]
    _save(data)
    return True


def get_close_attention_enabled(user_id: int) -> bool:
    """Включён ли режим «пристальное внимание» для пользователя."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    return bool(u.get("close_attention_enabled") if u else False)


def get_factcheck_enabled(user_id: int) -> bool:
    """Включён ли факт-чек для пользователя."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    return bool(u.get("factcheck_enabled") if u else False)


def set_factcheck_enabled(user_id: int, enabled: bool) -> bool:
    """Включить/выключить факт-чек для пользователя."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, "")
    data["users"][key]["factcheck_enabled"] = bool(enabled)
    _save(data)
    return True


def set_close_attention_enabled(user_id: int, enabled: bool) -> bool:
    """Включить/выключить режим «пристальное внимание»."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id)
    data["users"][key]["close_attention_enabled"] = bool(enabled)
    _save(data)
    return True


def get_close_attention_views(user_id: int) -> list[dict]:
    """Возвращает накопленные взгляды пользователя в режиме пристального внимания."""
    data = _load()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return []
    return list(u.get("close_attention_views") or [])


def append_close_attention_view(
    user_id: int,
    source: str,
    views: str,
    needs_evidence: bool,
    evidence_found: bool,
    display_name: str = "",
) -> None:
    """Добавляет запись о взглядах пользователя (режим пристального внимания)."""
    data = _load()
    key = str(user_id)
    if key not in data["users"]:
        data["users"][key] = _default_user(user_id, display_name)
    u = data["users"][key]
    if display_name:
        u["display_name"] = display_name
    archive = u.get("close_attention_views") or []
    archive.append({
        "date": date.today().isoformat(),
        "source": (source or "")[:300],
        "views": (views or "")[:1500],
        "needs_evidence": bool(needs_evidence),
        "evidence_found": bool(evidence_found),
    })
    u["close_attention_views"] = archive[-CLOSE_ATTENTION_VIEWS_LIMIT:]
    _save(data)


def format_close_attention_context(user_id: int, max_items: int = 15) -> str:
    """Форматирует накопленные взгляды для контекста ИИ (режим пристального внимания)."""
    views = get_close_attention_views(user_id)
    if not views:
        return ""
    items = list(reversed(views[-max_items:]))
    lines = []
    for i, v in enumerate(items, 1):
        dt = v.get("date", "")
        src = (v.get("source", "") or "")[:120]
        vw = (v.get("views", "") or "")[:200]
        ne = v.get("needs_evidence", False)
        ef = v.get("evidence_found", False)
        parts = [f"[{dt}] источник: {src}"]
        if vw:
            parts.append(f"взгляды: {vw}")
        if ne:
            parts.append(f"требовались доказательства: {'да' if ef else 'нет'}")
        lines.append(f"  {i}. {'; '.join(parts)}")
    return "Накопленные взгляды участника (режим пристального внимания):\n" + "\n".join(lines)


def get_effective_tone(u: dict) -> str:
    """Возвращает настроение для отображения: ручное или авто."""
    override = (u.get("tone_override") or "").strip()
    if override:
        return override
    return (u.get("tone_to_bot") or "").strip()


def get_stats_for_log() -> str:
    """Формирует текст статистики по всем пользователям для вывода в лог."""
    data = _load()
    users = data.get("users", {})
    if not users:
        return "База участников пуста."
    lines = ["=== Статистика пользователей ===", f"Всего: {len(users)}", ""]
    for uid, u in sorted(users.items(), key=lambda x: -x[1].get("stats", {}).get("total_messages", 0)):
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
        tone = get_effective_tone(u)
        override = (u.get("tone_override") or "").strip()
        if tone:
            lines.append(f"  настроение к боту: {tone}" + (" (ручное)" if override else " (авто)"))
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
    for uid, u in sorted(users.items(), key=lambda x: -x[1].get("stats", {}).get("total_messages", 0)):
        name = escape(str(u.get("display_name") or uid))
        rank = u.get("rank", "unknown")
        em = rank_emoji.get(rank, "❓")
        s = u.get("stats", {})
        pol = s.get("political_messages", 0)
        warn = s.get("warnings_received", 0)
        tone = get_effective_tone(u)
        tone_word = tone.split(",")[0].strip().split()[0] if tone else ""
        part = f"{em} {name} — {rank}"
        if tone_word:
            part += f" | к боту: {escape(tone_word)}"
        if pol or warn:
            part += f" (полит.: {pol}, замечаний: {warn})"
        lines.append(part)
    return "\n".join(lines)[:4000]
