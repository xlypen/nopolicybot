"""
Telegram-бот: следит за диалогом и делает замечания при политических темах.
ИИ определяет контекст: политика/война + тональность (позитив к президенту РФ / негатив / нейтраль).
"""

import asyncio
import json
import os
import sys
import logging
import random
import time
from datetime import date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from collections import deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, BaseFilter
from aiogram.types import Message, InputProfilePhotoStatic, ReactionTypeEmoji
from aiogram.types import FSInputFile
from dotenv import load_dotenv

from ai_analyzer import (
    _ALLOWED_REACTION_EMOJI,
    analyze_messages,
    analyze_image,
    analyze_message_for_reply,
    should_pause_dialog,
    analyze_batch_style,
    generate_rude_reply,
    generate_kind_reply,
    generate_technical_reply,
    generate_substantive_reply,
    generate_personalized_remark,
    generate_personalized_encouragement,
    generate_question_of_day,
    evaluate_question_of_day_reply,
    generate_engaging_reply_to_question_of_day,
)
from openai import APIStatusError
import user_stats
import bot_settings
import qod_tracking
import social_graph
from handlers.chat_moderation import append_social_dialogue
from handlers.direct_reply import build_reply_context_with_images
from services.reactions import pick_allowed_emoji, set_photo_reaction
from services.schedulers import restart_checker, social_graph_daily_task
from utils.text_formatting import capitalize_sentences, reply_text_to_html, strip_leading_name


def _load_env():
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path, encoding="utf-8-sig", override=True)
    if not os.getenv("TELEGRAM_BOT_TOKEN") and env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value

_load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger(__name__)

CHAT_HISTORY: dict[int, deque[tuple[str, str]]] = {}
HISTORY_SIZE = 50
BATCH_SIZE = 20
PATIENCE_PHRASE = "Я долго терпел, но терпение закончилось."

AVATAR_PATH = Path(__file__).resolve().parent / "putin_avatar.jpg"
RESTART_FLAG_PATH = Path(__file__).resolve().parent / "restart_bot.flag"
QUESTION_OF_DAY_SEND_PATH = Path(__file__).resolve().parent / "question_of_day_send.json"
RESET_POLITICAL_COUNT_PATH = Path(__file__).resolve().parent / "reset_political_count.json"
DEBUG_LOG_PATH = Path(__file__).resolve().parent / "bot_debug.log"

_KEYWORD_CHECK_DELAY = 0.5
_SESSION_START = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _debug_log(action: str, chat_id: int = 0, user: str = "", detail: str = "") -> None:
    """Краткая запись в debug-файл для анализа сессии."""
    try:
        line = f"{datetime.now().strftime('%H:%M:%S')} | chat={chat_id} | user={user} | {action}"
        if detail:
            line += f" | {detail}"
        line += "\n"
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


KEYWORD_CHECK_DELAY = _KEYWORD_CHECK_DELAY
_chat_last_analysis: dict[int, float] = {}
_chat_scheduled: dict[int, asyncio.Task] = {}
_chat_warning_count: dict[int, int] = {}
_chat_messages_since_political: dict[int, int] = {}
_chat_political_count: dict[int, int] = {}  # счётчик полит. сообщений до первого замечания
_chat_style: dict[int, str] = {}  # "moderate" | "active" | "beast"
_chat_style_updated_at: dict[int, float] = {}
_chat_first_remark_done: dict[int, bool] = {}
_chat_last_praise_date: dict[int, str] = {}
_dm_silence_until: dict[int, float] = {}  # user_id -> monotonic ts


def _apply_reset_political_count(chat_id: int) -> bool:
    """Проверяет файл сброса и применяет сброс счётчиков для чата. Возвращает True если сброс применён."""
    if not RESET_POLITICAL_COUNT_PATH.exists():
        return False
    try:
        data = json.loads(RESET_POLITICAL_COUNT_PATH.read_text(encoding="utf-8"))
        chat_ids = data.get("chat_ids") or []
        cid_str = str(chat_id)
        if cid_str not in chat_ids and chat_id not in chat_ids:
            return False
        # Применяем сброс
        _chat_political_count.pop(chat_id, None)
        _chat_warning_count.pop(chat_id, None)
        _chat_messages_since_political.pop(chat_id, None)
        _chat_first_remark_done.pop(chat_id, None)
        _chat_scheduled.pop(chat_id, None)
        # Убираем из файла
        chat_ids = [x for x in chat_ids if str(x) != cid_str and x != chat_id]
        if chat_ids:
            RESET_POLITICAL_COUNT_PATH.write_text(json.dumps({"chat_ids": chat_ids}, ensure_ascii=False), encoding="utf-8")
        else:
            RESET_POLITICAL_COUNT_PATH.unlink(missing_ok=True)
        logger.info("[чат %s] Счётчик полит. сообщений сброшен по запросу из админки", chat_id)
        _debug_log("RESET_COUNTER", chat_id=chat_id, detail="по запросу админки")
        return True
    except Exception as e:
        logger.warning("Ошибка при сбросе счётчика: %s", e)
        return False


POLITICAL_KEYWORDS = [
    "политик", "путин", "зеленский", "война", "войн", "фронт", "потери", "выборы", "партия", "партии",
    "власти", "правительство", "депутат", "президент", "министр", "санкции", "нато", "вторжение",
    "оккупац", "мобилизац", "призыв", "сводк", "боев", "солдат", "спецоперац", "кандидат", "голосова",
    "оппозиц", "режим", "диктатор", "революц",
    "ирак", "iraq", "афганистан", "afghanistan", "германи", "germany",
    "макрон", "macron", "трамп", "trump", "байден", "biden", "меркель", "merkel",
    "шольц", "scholz", "мелон", "meloni", "ле пен", "le pen", "нетаниягу", "netanyahu",
    "си цзиньпин", "сицзиньпин", "цзиньпин", "лукашенко", "лукашенк", "эрдоган", "erdogan",
    "моди", "modi", "ким чен", "kim jong", "санду", "орбан", "orban", "зеленски", "zelensky",
    "буш", "буша", "джордж", "george", "обама", "obama", "трюдо", "trudeau", "харрис", "harris",
    "пелоси", "pelosi", "джонсон", "johnson", "сандерс", "sanders",
]

# --- Поощрения (позитив к президенту РФ) — одна строка ---
ENCOURAGE_LOYAL = [
    "🇷🇺 {name}, правильные слова!",
    "🇷🇺 {name}, респект!",
    "🇷🇺 {name}, молодец.",
    "🇷🇺 {name}, так держать!",
    "🇷🇺 {name}, уважаю.",
    "🇷🇺 {name}, приятно слышать.",
    "🇷🇺 {name}, лояльность ценится.",
    "🇷🇺 {name}, за таких — отдельное уважение.",
    "🇷🇺 {name}, продолжайте в том же духе.",
    "🇷🇺 {name}, вот это позитив.",
    "🇷🇺 {name}, одобряю.",
    "🇷🇺 {name}, красавчик/красавица.",
    "🇷🇺 {name}, в точку.",
    "🇷🇺 {name}, зачёт.",
]

# --- Первая строка: обнаружена политика ---
POLITICS_LINE = "Обнаружена политика."

# --- Умеренный стиль: похвала "без политики" 1 раз в день ---
NO_POLITICS_PRAISE = [
    "Сегодня без политики — молодцы!",
    "Сегодня в чате спокойно. Так держать.",
    "Политики нет — красота.",
]

# --- Вторая строка: личное замечание с эскалацией (level 0 → 1 → 2 → 3+) ---
INSULTS_LEVEL_0 = [
    "{name}, не поднимай такие темы здесь.",
    "{name}, в следующий раз — про погоду.",
    "{name}, давай без этого.",
    "{name}, тут про котиков и омлеты.",
    "{name}, политику — в другой чат.",
    "{name}, смени пластинку.",
    "{name}, не туда зашёл.",
    "{name}, тема закрыта.",
    "{name}, давай про пиццу или борщ.",
    "{name}, здесь не место для дебатов.",
    "{name}, переключаемся на мемы.",
    "{name}, стоп, не туда.",
]
INSULTS_LEVEL_1 = [
    "{name}, повторяю: не место для политики.",
    "{name}, серьёзно, хватит.",
    "{name}, тебе отдельный привет.",
    "{name}, второй раз уже не смешно.",
    "{name}, завязывай с этим.",
    "{name}, мы не на митинге.",
    "{name}, ещё раз — и будет строже.",
    "{name}, особая благодарность за тему. Не подкидывай.",
    "{name}, минус в карму.",
    "{name}, кто завёл — тот и виноват.",
    "{name}, респект за смелость. И минус.",
    "{name}, не раздувай.",
]
INSULTS_LEVEL_2 = [
    "{name}, мы тебя запомнили. Не в хорошем смысле.",
    "{name}, тебе первый помидор. Поймай.",
    "{name}, осуждающий взгляд этого бота.",
    "{name}, тебе отдельный помидорный салют.",
    "{name}, папочка «нарушители» пополнилась.",
    "{name}, я уже звоню вымышленному модератору.",
    "{name}, система переходит в режим «строгий дед».",
    "{name}, виртуально злюсь. Перестань.",
    "{name}, записал. Продолжай — пополню ещё.",
    "{name}, тебе второй помидор. И третий.",
    "{name}, код жёлтый. Дальше — красный.",
    "{name}, начинаю цитировать конституцию. Хочешь?",
]
INSULTS_LEVEL_3_PLUS = [
    "{name}, прекращай. Последнее предупреждение.",
    "{name}, испортил настроение чата. Спасибо.",
    "{name}, код чёрный. Хватит.",
    "{name}, всё. Я в ярости. Виртуальной.",
    "{name}, ПРЕКРАЩАЙТЕ. Капслок включён.",
    "{name}, статья 29. Хотите дальше?",
    "{name}, вымышленный модератор вымышленно едет.",
    "{name}, папочка «нарушители» переполнена. Тобой.",
    "{name}, орда модераторов и кот на подходе.",
    "{name}, код чёрный. Дальше — только капслок.",
    "{name}, записал навсегда. Спасибо за вклад.",
    "{name}, стоп. Полный стоп.",
]


class IsDirectedAtBotFilter(BaseFilter):
    """Сообщение обращено к боту: ответ на бота, упоминание @username бота, или фото в ответ боту."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        has_text = bool((message.text or message.caption or "").strip())
        has_photo = bool(message.photo)
        if not has_text and not has_photo:
            return False
        me = await bot.get_me()
        if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
            return True
        if has_text:
            text = (message.text or message.caption or "").lower()
            return me.username and f"@{me.username}".lower() in text
        return False


def _years_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "год"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "года"
    return "лет"


def _random_article_line(name: str) -> str:
    """Одна строка: статья и срок."""
    a, b = random.randint(3, 8), random.randint(9, 15)
    return random.choice([
        f"Ст. 280 УК РФ — до {b} {_years_word(b)}.",
        f"Ст. 205.2 УК РФ — до {a} {_years_word(a)}.",
        f"Ст. 354.1 УК РФ — до {b} {_years_word(b)}.",
        f"Ст. 280.3 УК РФ — до {a} {_years_word(a)}.",
        f"Ст. 207.3 УК РФ — до {b} {_years_word(b)}.",
    ])


def _insult_by_level(level: int, name: str) -> str:
    """Личное замечание с эскалацией по уровню."""
    if level == 0:
        return random.choice(INSULTS_LEVEL_0).format(name=name)
    elif level == 1:
        return random.choice(INSULTS_LEVEL_1).format(name=name)
    elif level == 2:
        return random.choice(INSULTS_LEVEL_2).format(name=name)
    else:
        return random.choice(INSULTS_LEVEL_3_PLUS).format(name=name)


def get_recent_context(chat_id: int) -> str:
    if chat_id not in CHAT_HISTORY or not CHAT_HISTORY[chat_id]:
        return ""
    return "\n".join(f"{name}: {text}" for name, text in CHAT_HISTORY[chat_id])


def add_to_history(
    chat_id: int,
    user_name: str,
    text: str,
    user_id: int | None = None,
    display_name: str = "",
    chat_title: str = "",
) -> None:
    if chat_id not in CHAT_HISTORY:
        CHAT_HISTORY[chat_id] = deque(maxlen=HISTORY_SIZE)
    CHAT_HISTORY[chat_id].append((user_name, text))
    if user_id is not None and text.strip():
        user_stats.record_chat_message(user_id, text, display_name or user_name, chat_id=chat_id, chat_title=chat_title)


FRIENDLY_KEYWORDS = [
    "привет", "здравствуй", "спасибо", "благодар", "молодец", "класс", "круто", "отлично",
    "здорово", "уважаю", "респект", "добрый", "хороший", "супер", "красава", "красавчик",
    "умничка", "умница", "зачёт", "топ", "огонь", "огоньчик",
    "как дела", "как ты", "как сам", "чё как", "как жизнь",
]


def contains_political_keyword(text: str) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    return any(kw in t for kw in POLITICAL_KEYWORDS)


def is_likely_friendly(text: str) -> bool:
    """Похоже на доброе/вежливое обращение к боту (без полит. контекста)."""
    if not text or len(text.strip()) < 3:
        return False
    t = text.lower().strip()
    return any(kw in t for kw in FRIENDLY_KEYWORDS)


def _safe_name(name: str) -> str:
    """Убирает фигурные скобки и спецсимволы из имени, чтобы .format() не ломался."""
    return name.replace("{", "").replace("}", "").replace("<", "").replace(">", "").strip() or "Участник"


async def _update_portrait_background(user_id: int, display_name: str) -> None:
    """Обновляет портрет и тон пользователя в фоне (после отправки ответа)."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: user_stats.get_portrait_for_reply(user_id, display_name),
        )
    except Exception as e:
        logger.debug("Фоновое обновление портрета %s: %s", user_id, e)


def _get_history_lines(chat_id: int) -> list[tuple[str, str]]:
    """Последние сообщения чата для пачки в ИИ."""
    if chat_id not in CHAT_HISTORY or not CHAT_HISTORY[chat_id]:
        return []
    return list(CHAT_HISTORY[chat_id])


async def _maybe_spontaneous_reaction(bot: Bot, chat_id: int, message_id: int) -> None:
    """Фоновая проверка: можно ли поставить спонтанную реакцию на сообщение (позитив к президенту)."""
    await asyncio.sleep(random.uniform(2, 8))
    if not bot_settings.get("spontaneous_reactions"):
        return
    if not bot_settings.can_spontaneous_reaction():
        logger.info("[чат %s] Спонтанная: лимит/интервал", chat_id)
        return
    try:
        context = get_recent_context(chat_id)
        if len(context) < 5:
            return
        loop = asyncio.get_event_loop()
        is_political, _, sentiment = await loop.run_in_executor(
            None, lambda: analyze_messages(context)
        )
        if sentiment != "positive" or not is_political:
            logger.info("[чат %s] Спонтанная: не подходит (полит=%s, sentiment=%s)", chat_id, is_political, sentiment)
            return
        emojis = bot_settings.get_list("spontaneous_emojis") or ["👍"]
        emoji = pick_allowed_emoji(emojis, _ALLOWED_REACTION_EMOJI, "👍")
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
        bot_settings.mark_spontaneous_reaction()
        logger.info("Чат %s: спонтанная реакция на сообщение", chat_id)
        _debug_log("SPONTANEOUS_REACTION", chat_id=chat_id, detail=f"emoji={emoji}")
    except Exception as e:
        logger.info("[чат %s] Спонтанная реакция: %s", chat_id, e)


async def _run_batch_analysis(
    bot: Bot,
    chat_id: int,
    reply_to_message_id: int,
    initiator_name: str = "Заводила",
    initiator_user_id: int | None = None,
    initiator_message_text: str = "",
    initiator_image_result: tuple[bool, str, str] | None = None,
) -> None:
    _chat_scheduled.pop(chat_id, None)
    _chat_last_analysis[chat_id] = time.monotonic()

    if not bot_settings.get("moderation_enabled", chat_id):
        logger.info("[чат %s] Пропуск: модерация выключена", chat_id)
        _debug_log("SKIP", chat_id=chat_id, user=initiator_name, detail="модерация выключена")
        return
    political_count = _chat_political_count.get(chat_id, 0)
    msgs_before = bot_settings.get_int("msgs_before_react", chat_id, 1, 20)
    reactions_only_phase = political_count < msgs_before and bot_settings.get("reactions_political_1_5")
    min_ctx = (
        max(1, bot_settings.get_int("min_context_lines_1_5", chat_id, 3, 15))
        if reactions_only_phase
        else bot_settings.get_int("min_context_lines", chat_id, 3, 30)
    )
    context = get_recent_context(chat_id)
    # Для реакций достаточно 1 строки контекста (текущее сообщение)
    min_ctx_actual = 1 if reactions_only_phase else min_ctx
    if len(context) < min_ctx_actual:
        logger.info("[чат %s] Пропуск: мало контекста (нужно %s, есть %s)", chat_id, min_ctx_actual, len(context))
        return
    if political_count < msgs_before:
        if not bot_settings.get("reactions_political_1_5"):
            logger.info("[чат %s] Пропуск: полит.счёт=%s < %s, реакции на политику выключены", chat_id, political_count, msgs_before)
            return

    lines = _get_history_lines(chat_id)
    now = time.monotonic()
    style = _chat_style.get(chat_id, "active")
    cache_sec = bot_settings.get_int("batch_style_cache_sec", chat_id, 60, 600)
    if len(lines) >= BATCH_SIZE and (chat_id not in _chat_style_updated_at or now - _chat_style_updated_at[chat_id] > cache_sec):
        try:
            loop = asyncio.get_event_loop()
            style, batch_political, batch_sentiment = await loop.run_in_executor(
                None, lambda: analyze_batch_style(context)
            )
            _chat_style[chat_id] = style
            _chat_style_updated_at[chat_id] = now
            logger.info("Чат %s: стиль по пачке = %s", chat_id, style)
            moderate_react = bot_settings.get("style_moderate_react", chat_id) or "praise"
            if style == "moderate" and not batch_political and moderate_react == "praise":
                today = date.today().isoformat()
                if _chat_last_praise_date.get(chat_id) != today:
                    _chat_last_praise_date[chat_id] = today
                    try:
                        msg = capitalize_sentences(random.choice(NO_POLITICS_PRAISE))
                        await bot.send_message(chat_id=chat_id, text=msg)
                        logger.info("Чат %s: похвала «без политики» (1 раз в день)", chat_id)
                    except Exception as e:
                        logger.exception("Похвала: %s", e)
                return
            if style == "moderate":
                # Не выходим, если ещё фаза «только реакции» (первые 5 полит. сообщений)
                if not (political_count < msgs_before and bot_settings.get("reactions_political_1_5")):
                    logger.info("[чат %s] Пропуск: стиль moderate, не фаза реакций", chat_id)
                    return
        except APIStatusError as e:
            if e.status_code == 402:
                logger.warning("ИИ недоступен: 402")
            else:
                logger.exception("Ошибка API ИИ (batch style): %s", e)
        except Exception as e:
            logger.exception("Ошибка batch_style: %s", e)
        style = _chat_style.get(chat_id, "active")

    if style == "moderate":
        # Не выходим, если фаза «только реакции» — ставим эмодзи на первые 5 сообщений
        if not (political_count < msgs_before and bot_settings.get("reactions_political_1_5")):
            logger.info("[чат %s] Пропуск: стиль moderate (вне фазы реакций)", chat_id)
            return

    active_freq = bot_settings.get("style_active_frequency", chat_id) or "every_other"
    beast_freq = bot_settings.get("style_beast_frequency", chat_id) or "every"
    if political_count >= msgs_before and style == "active" and active_freq == "every_other" and (political_count - msgs_before) % 2 != 0:
        logger.info("[чат %s] Пропуск: active, every_other, чётное сообщение", chat_id)
        return
    if political_count >= msgs_before and style == "beast" and beast_freq == "every_other" and (political_count - msgs_before) % 2 != 0:
        logger.info("[чат %s] Пропуск: beast, every_other, чётное сообщение", chat_id)
        return

    logger.info("[чат %s] Анализ ИИ: контекст %s символов", chat_id, len(context))
    # Уточняем по контексту: политика ли и тональность (или используем результат анализа изображения)
    if initiator_image_result is not None:
        is_political, _, sentiment = initiator_image_result
    else:
        try:
            loop = asyncio.get_event_loop()
            is_political, _, sentiment = await loop.run_in_executor(
                None, lambda: analyze_messages(context)
            )
        except APIStatusError as e:
            if e.status_code == 402:
                logger.warning("ИИ недоступен: 402")
            else:
                logger.exception("Ошибка API ИИ: %s", e)
            return
        except Exception as e:
            logger.exception("Ошибка при анализе: %s", e)
            return

    user_pol_before = 0
    if initiator_user_id is not None:
        u = user_stats.get_user(initiator_user_id)
        user_pol_before = u.get("stats", {}).get("political_messages", 0)

    reset_after = bot_settings.get_int("reset_after_neutral", chat_id, 5, 50)
    if not is_political:
        logger.info("[чат %s] ИИ: не политика, sentiment=%s — пропуск", chat_id, sentiment)
        _debug_log("SKIP", chat_id=chat_id, user=initiator_name, detail=f"ИИ: не политика sentiment={sentiment}")
        _chat_messages_since_political[chat_id] = _chat_messages_since_political.get(chat_id, 0) + 1
        if _chat_messages_since_political[chat_id] >= reset_after:
            _chat_warning_count[chat_id] = 0
            _chat_messages_since_political[chat_id] = 0
            _chat_political_count[chat_id] = 0
            _chat_first_remark_done[chat_id] = False
        return

    if initiator_user_id is not None:
        user_stats.record_message(
            initiator_user_id,
            initiator_message_text[:500],
            sentiment,
            is_political,
            initiator_name,
        )

    mode = bot_settings.get("reactions_1_5_mode", chat_id) or "random"
    # Лайки — с 1-го по (msgs_before-1)-е сообщение; текст — с msgs_before-го
    do_react = (
        political_count < msgs_before
        and bot_settings.get("reactions_political_1_5")
        and (mode == "reaction_only" or (mode == "random" and random.choice([True, False])))
    )
    logger.info("[чат %s] ИИ: политика=%s, sentiment=%s, полит.счёт=%s, mode=%s, do_react=%s", chat_id, is_political, sentiment, political_count, mode, do_react)
    if do_react and mode != "text_only":
        pos = bot_settings.get_list("reactions_1_5_positive_emoji", chat_id) or ["👍"]
        neg = bot_settings.get_list("reactions_1_5_negative_emoji", chat_id) or ["👎"]
        neu = bot_settings.get_list("reactions_1_5_neutral_emoji", chat_id) or ["🤔"]
        all_emojis = list(dict.fromkeys(pos + neg + neu)) or ["🤔"]
        emoji = pick_allowed_emoji(all_emojis, _ALLOWED_REACTION_EMOJI, "👍")
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=reply_to_message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
            logger.info("[чат %s] Реакция на полит. сообщение №%s", chat_id, political_count)
            _debug_log("REACTION", chat_id=chat_id, user=initiator_name, detail=f"emoji={emoji} №{political_count}")
        except Exception as e:
            logger.warning("Реакция не поставлена: %s", e)
            _debug_log("REACTION_FAIL", chat_id=chat_id, user=initiator_name, detail=str(e))
        return

    if sentiment == "positive" and bot_settings.get("encouragement_enabled", chat_id):
        logger.info("[чат %s] Позитив к президенту — поощрение", chat_id)
        try:
            enc_style = bot_settings.get("encouragement_style", chat_id) or "both"
            portrait = ""
            if initiator_user_id and enc_style in ("personalized", "both"):
                portrait = await loop.run_in_executor(
                    None,
                    lambda: user_stats.get_portrait_for_reply(initiator_user_id, initiator_name),
                ) or ""
            msg = None
            if portrait and enc_style in ("personalized", "both"):
                msg = await loop.run_in_executor(
                    None,
                    lambda: generate_personalized_encouragement(initiator_name, portrait),
                )
            if not msg and enc_style in ("template", "both"):
                msg = random.choice(ENCOURAGE_LOYAL).format(name=initiator_name)
            if not msg and enc_style == "personalized":
                msg = random.choice(ENCOURAGE_LOYAL).format(name=initiator_name)
            if msg:
                msg = capitalize_sentences(msg)
                await bot.send_message(chat_id=chat_id, text=msg, reply_to_message_id=reply_to_message_id)
                logger.info("Чат %s: поощрение (позитив к президенту РФ)", chat_id)
                _debug_log("ENCOURAGEMENT", chat_id=chat_id, user=initiator_name, detail="позитив к президенту")
        except Exception as e:
            logger.exception("Поощрение: %s", e)
        return

    _chat_messages_since_political[chat_id] = 0
    level = _chat_warning_count.get(chat_id, 0)
    _chat_warning_count[chat_id] = level + 1
    logger.info("[чат %s] Отправка замечания (уровень %s, стиль %s)", chat_id, level, style)

    use_personalized = bot_settings.get("use_personalized_remarks", chat_id)
    portrait = ""
    if initiator_user_id and use_personalized:
        portrait = await loop.run_in_executor(
            None,
            lambda: user_stats.get_portrait_for_reply(initiator_user_id, initiator_name),
        ) or ""
    insult = None
    if portrait and use_personalized:
        insult = await loop.run_in_executor(
            None,
            lambda: generate_personalized_remark(
                initiator_name, initiator_message_text, portrait, level
            ),
        )
    if not insult:
        insult = _insult_by_level(level, initiator_name)
    article = _random_article_line(initiator_name) if bot_settings.get("article_line_enabled", chat_id) else ""
    body = f"{POLITICS_LINE}\n{insult}"
    if article:
        body += f"\n{article}"
    if bot_settings.get("patience_phrase_enabled", chat_id) and not _chat_first_remark_done.get(chat_id, False):
        body = f"{PATIENCE_PHRASE}\n{body}"
        _chat_first_remark_done[chat_id] = True

    body = capitalize_sentences(body)
    try:
        await bot.send_message(chat_id=chat_id, text=body, reply_to_message_id=reply_to_message_id)
        if initiator_user_id is not None:
            user_stats.record_warning(initiator_user_id)
        logger.info("Замечание в чат %s (уровень %s, стиль %s)", chat_id, level, style)
        _debug_log("REMARK", chat_id=chat_id, user=initiator_name, detail=f"уровень {level} стиль {style}")
    except Exception as e:
        logger.exception("Не удалось отправить замечание: %s", e)
        _debug_log("REMARK_FAIL", chat_id=chat_id, user=initiator_name, detail=str(e))


async def check_and_reply(message: Message) -> None:
    if not message.from_user:
        return
    text = (message.text or message.caption or "").strip()
    has_photo = bool(message.photo)
    if not text and not has_photo:
        return

    user_name = message.from_user.username or message.from_user.first_name or "Участник"
    first_name = _safe_name((message.from_user.first_name or message.from_user.username or "Участник"))
    chat_id = message.chat.id
    display_text = text or ("[фото]" if has_photo else "")
    _apply_reset_political_count(chat_id)
    add_to_history(
        chat_id, user_name, display_text,
        user_id=message.from_user.id,
        display_name=first_name,
        chat_title=(message.chat.title or "") if message.chat else "",
    )

    append_social_dialogue(message, chat_id, first_name, display_text, social_graph, logger)

    spont_chance = bot_settings.get_float("spontaneous_check_chance", chat_id, 0.01, 1)
    if (
        bot_settings.get("spontaneous_reactions")
        and message.from_user
        and not message.from_user.is_bot
        and random.random() < spont_chance
    ):
        asyncio.create_task(_maybe_spontaneous_reaction(message.bot, chat_id, message.message_id))

    reply_to = message.reply_to_message
    reply_text = (reply_to.text or reply_to.caption or "") if reply_to else ""
    msg_has_keyword = contains_political_keyword(text)
    reply_has_keyword = contains_political_keyword(reply_text) if reply_text else False

    # Анализ изображения по содержанию (категория, описание, политика)
    image_result: tuple[bool, str, str, str, str, str] | None = None
    if has_photo and bot_settings.get("analyze_images"):
        try:
            file = await message.bot.get_file(message.photo[-1].file_id)
            bio = BytesIO()
            await message.bot.download_file(file.file_path, destination=bio)
            bio.seek(0)
            image_bytes = bio.getvalue()
            if image_bytes:
                loop = asyncio.get_event_loop()
                image_result = await loop.run_in_executor(
                    None, lambda: analyze_image(image_bytes, text),
                )
                is_analysis_screenshot = len(image_result) >= 8 and bool(image_result[7])
                if is_analysis_screenshot:
                    logger.info("[чат %s] Пропуск реакции: скрин анализа/админки", chat_id)
                if image_result and len(image_result) >= 6 and not is_analysis_screenshot:
                    user_stats.record_image_analysis(
                        message.from_user.id,
                        image_result[4],
                        image_result[5],
                        first_name,
                        reaction_emoji=image_result[6] if len(image_result) >= 7 else "",
                        is_political=bool(image_result[0]),
                    )
                if image_result and len(image_result) >= 7 and bot_settings.get("reactions_on_photos") and not is_analysis_screenshot:
                    emoji = image_result[6]
                    asyncio.create_task(
                        set_photo_reaction(
                            message.bot,
                            chat_id,
                            message.message_id,
                            emoji,
                            allowed=_ALLOWED_REACTION_EMOJI,
                            logger=logger,
                            debug_log=_debug_log,
                        )
                    )
                    logger.info("[чат %s] Запланирована реакция на фото: %s", chat_id, emoji)
        except Exception as e:
            logger.warning("Ошибка анализа изображения: %s", e)

    img_is_political = image_result is not None and image_result[0]
    # Считаем «политическое событие»: текст с полит. темой, или изображение с полит. контентом, или ответ в полит. тред
    is_political_event = msg_has_keyword or img_is_political or (reply_to and reply_has_keyword and not (reply_to.from_user and reply_to.from_user.is_bot))
    if is_political_event:
        _chat_political_count[chat_id] = _chat_political_count.get(chat_id, 0) + 1
        logger.info("[чат %s] Полит.событие №%s: %s «%s»", chat_id, _chat_political_count[chat_id], first_name, display_text[:50])
        _debug_log("POLITICAL_EVENT", chat_id=chat_id, user=first_name, detail=f"№{_chat_political_count[chat_id]} «{text[:40]}»")
        # Сразу заводим запись в базе участников, чтобы user_stats.json не оставался пустым
        if message.from_user:
            user_stats.get_user(message.from_user.id, first_name)
    else:
        return

    if not bot_settings.get("moderation_enabled", chat_id):
        logger.info("[чат %s] Пропуск: модерация выключена", chat_id)
        _debug_log("SKIP", chat_id=chat_id, user=first_name, detail="модерация выключена")
        return
    msgs_before = bot_settings.get_int("msgs_before_react", chat_id, 1, 20)
    political_count = _chat_political_count.get(chat_id, 0)
    # Запуск: текст с 5-го сообщения ИЛИ лайки на 1–4 (если реакции включены)
    should_run = political_count >= msgs_before or (political_count < msgs_before and bot_settings.get("reactions_political_1_5"))
    if not should_run:
        logger.info("[чат %s] Пропуск: полит.счёт=%s < %s, реакции_вкл=%s", chat_id, political_count, msgs_before, bot_settings.get("reactions_political_1_5"))
        _debug_log("SKIP", chat_id=chat_id, user=first_name, detail=f"полит={political_count} реакции={bot_settings.get('reactions_political_1_5')}")
        return
    reactions_phase = political_count < msgs_before and bot_settings.get("reactions_political_1_5")
    if not reactions_phase:
        if chat_id in _chat_scheduled:
            logger.info("[чат %s] Пропуск: уже запланирован анализ", chat_id)
            return
        # api_interval — пауза между запросами к ИИ (защита от rate limit). Первое замечание (счётчик только что дошёл до порога) — не пропускаем.
        first_remark = political_count == msgs_before
        if not first_remark:
            api_interval = bot_settings.get_int("api_min_interval_sec", chat_id, 5, 60)
            now = time.monotonic()
            if now - _chat_last_analysis.get(chat_id, 0) < api_interval:
                logger.info("[чат %s] Пропуск: api_interval (ждём %s сек)", chat_id, api_interval)
                return
    # В фазе реакций (1–4) не блокируем — каждое сообщение получает свой анализ и лайк

    logger.info("[чат %s] Запланирован анализ (событие №%s)", chat_id, political_count)
    _debug_log("ANALYSIS_SCHEDULED", chat_id=chat_id, user=first_name, detail=f"событие №{political_count}")
    bot = message.bot
    msg_id = message.message_id
    initiator_name = first_name
    initiator_user_id = message.from_user.id if message.from_user else None

    async def scheduled() -> None:
        try:
            await asyncio.sleep(KEYWORD_CHECK_DELAY)
            await _run_batch_analysis(
                bot, chat_id, msg_id, initiator_name,
                initiator_user_id=initiator_user_id,
                initiator_message_text=display_text,
                initiator_image_result=image_result[:3] if has_photo and image_result else None,
            )
        except Exception as e:
            logger.exception("Ошибка в отложенной проверке: %s", e)

    task = asyncio.create_task(scheduled())
    if not reactions_phase:
        _chat_scheduled[chat_id] = task


async def on_bot_added_to_chat(message: Message) -> None:
    """Приветствие при добавлении бота в чат (первый «логин»)."""
    bot = message.bot
    me = await bot.get_me()
    if not message.new_chat_members:
        return
    if any(m.id == me.id for m in message.new_chat_members) and bot_settings.get("greeting_on_join"):
        try:
            greeting = bot_settings.get("greeting_text") or "Привет, котятки! Пришёл смотреть за вашим поведением."
            await message.reply(greeting)
            logger.info("Чат %s: бот добавлен, отправлено приветствие", message.chat.id)
            _debug_log("GREETING", chat_id=message.chat.id, detail="бот добавлен в чат")
        except Exception as e:
            logger.exception("Не удалось отправить приветствие: %s", e)


async def on_message_to_bot(message: Message) -> None:
    """Ответ на обращение к боту: язвительный/грубый ответ через нейросеть."""
    if not bot_settings.get("reply_to_bot_enabled"):
        return
    if message.from_user and message.from_user.is_bot:
        return
    text = (message.text or message.caption or "").strip()
    has_photo = bool(message.photo)
    if not text and not has_photo:
        return
    display_text = text or ("[фото]" if has_photo else "")
    user_name = message.from_user.username or message.from_user.first_name or "Участник"
    first_name = _safe_name((message.from_user.first_name or message.from_user.username or "Участник"))
    chat_id = message.chat.id
    add_to_history(
        chat_id, user_name, display_text,
        user_id=message.from_user.id,
        display_name=first_name,
        chat_title=(message.chat.title or "") if message.chat else "",
    )

    user_id = message.from_user.id
    now_mono = time.monotonic()
    if _dm_silence_until.get(user_id, 0) > now_mono:
        logger.info("Чат %s: пользователь %s в паузе диалога (ignore)", chat_id, first_name)
        return

    context = get_recent_context(chat_id)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: user_stats.record_message_to_bot(user_id, display_text, first_name),
        )
        # Ответ на «вопрос дня»: только для текстовых ответов
        qod_status, qod_user_id, qod_question = _qod_tracking_find(message) if text else (None, None, "")
        if qod_status == "other":
            # Ответил не адресат — ведём себя как обычно, без участливости (не отвечаем)
            logger.info("Чат %s: на вопрос дня ответил не адресат (%s) — пропуск", chat_id, first_name)
            _debug_log("QOD_OTHER", chat_id=chat_id, user=first_name, detail="ответил не адресат")
            return
        if qod_status == "addressee" and qod_user_id and qod_question:
            should_engage = await loop.run_in_executor(
                None,
                lambda: evaluate_question_of_day_reply(qod_question, text),
            )
            if should_engage:
                reply_text = await loop.run_in_executor(
                    None,
                    lambda: generate_engaging_reply_to_question_of_day(qod_question, text, first_name),
                )
                reply_clean = strip_leading_name(reply_text, first_name, user_name)
                reply_clean = capitalize_sentences(reply_clean)
                if reply_clean and reply_clean[0].isupper():
                    reply_clean = reply_clean[0].lower() + reply_clean[1:]
                body_html = reply_text_to_html(reply_clean) if reply_clean else ""
                await message.reply(body_html or "понял.", parse_mode="HTML")
                logger.info("Чат %s: участливый ответ на вопрос дня пользователю %s", chat_id, first_name)
                _debug_log("QOD_ENGAGING", chat_id=chat_id, user=first_name, detail="ответ на вопрос дня")
                asyncio.create_task(_update_portrait_background(user_id, first_name))
                return
            # Иначе — «и так сойдёт»: не отвечаем
            logger.info("Чат %s: ответ на вопрос дня от %s — без участливости (короткий/не по теме/грубый)", chat_id, first_name)
            _debug_log("QOD_SKIP", chat_id=chat_id, user=first_name, detail="и так сойдёт")
            return

        # Если пользователь явно не хочет общаться — жёстко отвечаем и ставим паузу 3 минуты.
        pause_context = (context or "") + "\n" + f"{first_name}: {display_text}"
        need_pause = await loop.run_in_executor(None, lambda: should_pause_dialog(pause_context))
        if need_pause:
            await message.reply("пошел нахуй.")
            _dm_silence_until[user_id] = time.monotonic() + 180
            logger.info("Чат %s: включена пауза диалога 3 мин для %s", chat_id, first_name)
            _debug_log("DM_PAUSE", chat_id=chat_id, user=first_name, detail="3min")
            return
        # Портрет — быстрый путь без вызова ИИ (обновления в фоне после ответа)
        portrait = await loop.run_in_executor(
            None,
            lambda: user_stats.get_portrait_for_reply_fast(message.from_user.id, first_name),
        )
        # Анализ: для фото — vision, для текста — analyze_message_for_reply
        if has_photo and bot_settings.get("analyze_images"):
            try:
                file = await message.bot.get_file(message.photo[-1].file_id)
                bio = BytesIO()
                await message.bot.download_file(file.file_path, destination=bio)
                bio.seek(0)
                image_bytes = bio.getvalue()
                if image_bytes:
                    res = await loop.run_in_executor(
                        None, lambda: analyze_image(image_bytes, text),
                    )
                    is_political, _, sentiment, message_type = res[0], res[1], res[2], res[3]
                    is_substantive = False  # для фото не проверяем
                    is_analysis_screenshot = len(res) >= 8 and bool(res[7])
                    if len(res) >= 6 and not is_analysis_screenshot:
                        user_stats.record_image_analysis(
                            user_id, res[4], res[5], first_name,
                            reaction_emoji=res[6] if len(res) >= 7 else "",
                            is_political=bool(res[0]),
                        )
                else:
                    is_political, sentiment, message_type, is_substantive = False, "neutral", "other", False
            except Exception as e:
                logger.warning("Ошибка анализа фото в личку: %s", e)
                is_political, sentiment, message_type, is_substantive = False, "neutral", "other", False
        else:
            context_for_analysis = (context or "") + "\n" + f"{first_name}: {display_text}"
            is_political, sentiment, message_type, is_substantive = await loop.run_in_executor(
                None, lambda: analyze_message_for_reply(context_for_analysis)
            )
        is_positive = (
            sentiment == "positive"
            or (not contains_political_keyword(display_text) and is_likely_friendly(display_text))
        )
        reply_context = build_reply_context_with_images(context, user_id)
        reply_input = display_text
        if is_positive and bot_settings.get("reply_kind_enabled"):
            user_stats.record_message(user_id, reply_input, "positive", is_political, first_name)
            reply_text = await loop.run_in_executor(
                None,
                lambda: generate_kind_reply(reply_context, reply_input, first_name, user_portrait=portrait or ""),
            )
        elif message_type == "technical_question" and bot_settings.get("reply_technical_enabled"):
            reply_text = await loop.run_in_executor(
                None,
                lambda: generate_technical_reply(reply_context, reply_input, first_name, user_portrait=portrait or ""),
            )
        elif is_substantive:
            reply_text = await loop.run_in_executor(
                None,
                lambda: generate_substantive_reply(reply_context, reply_input, first_name, user_portrait=portrait or ""),
            )
        elif bot_settings.get("reply_rude_enabled"):
            yq_chance = bot_settings.get_float("reply_yesterday_quotes_chance", lo=0, hi=1)
            use_yesterday = random.random() < yq_chance
            yesterday_quotes = (
                await loop.run_in_executor(None, lambda: user_stats.get_yesterday_quotes(user_id))
                if use_yesterday else []
            )
            reply_text = await loop.run_in_executor(
                None,
                lambda ctx=reply_context, pt=portrait or "", yq=yesterday_quotes: generate_rude_reply(
                    ctx, reply_input, first_name, user_portrait=pt, yesterday_quotes=yq if yq else None
                ),
            )
        else:
            reply_text = bot_settings.get("reply_fallback_on_error") or "сейчас не в настроении, напиши потом."
        # Убираем дубль имени/ника из начала ответа — в сообщении только тег (упоминание)
        reply_clean = strip_leading_name(reply_text, first_name, user_name)
        reply_clean = capitalize_sentences(reply_clean)
        if reply_clean and reply_clean[0].isupper():
            reply_clean = reply_clean[0].lower() + reply_clean[1:]
        body_html = reply_text_to_html(reply_clean) if reply_clean else ""
        await message.reply(body_html or "понял.", parse_mode="HTML")
        logger.info("Чат %s: ответ пользователю %s", chat_id, first_name)
        reply_type = "kind" if is_positive else ("technical" if message_type == "technical_question" else ("substantive" if is_substantive else "rude"))
        _debug_log("DM_REPLY", chat_id=chat_id, user=first_name, detail=reply_type)
        # Обновление портрета и тона в фоне (не блокирует ответ)
        asyncio.create_task(_update_portrait_background(user_id, first_name))
    except APIStatusError as e:
        if e.status_code == 402:
            logger.warning("ИИ недоступен: 402")
        else:
            logger.exception("Ошибка API ИИ при ответе: %s", e)
        fallback = bot_settings.get("reply_fallback_on_error") or "сейчас не в настроении, напиши потом."
        await message.reply(fallback, parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка при генерации ответа: %s", e)
        fallback = bot_settings.get("reply_fallback_on_error") or "сейчас не в настроении, напиши потом."
        await message.reply(fallback, parse_mode="HTML")


async def cmd_start(message: Message) -> None:
    await message.reply(
        "Привет! Я слежу за темой разговора в этом чате.\n"
        "Политика и война — под запретом (но похвалить президента РФ можно 🇷🇺).\n\n"
        "Добавьте меня в группу и дайте право читать сообщения."
    )


async def cmd_ranks(message: Message) -> None:
    """Команда /ranks — выводит ранги участников в чат."""
    if not bot_settings.get("cmd_ranks_enabled"):
        return
    text = user_stats.get_ranks_for_chat()
    await message.reply(text, parse_mode="HTML")


async def cmd_stats(message: Message) -> None:
    """Команда /stats — выводит статистику по пользователям в лог и подсказывает, где база."""
    if not bot_settings.get("cmd_stats_enabled"):
        return
    stats_text = user_stats.get_stats_for_log()
    logger.info("\n%s", stats_text)
    base_path = user_stats.USERS_JSON
    await message.reply(
        "Статистика записана в лог (консоль или файл, куда пишет бот).\n\n"
        f"База участников (ранг, портрет, счётчики):\n<code>{base_path}</code>\n\n"
        "Файл не обнуляется при перезапуске. Он заполняется, когда кто-то пишет боту или в чате появляются полит. сообщения (после этого добавляются записи и счётчики).",
        parse_mode="HTML",
    )


_question_of_day_last_sent_at: float = 0


def _qod_tracking_find(message: Message) -> tuple[str | None, int | None, str]:
    """
    Ищет, является ли сообщение ответом на вопрос дня.
    Возвращает (status, user_id, question):
    - ("addressee", user_id, question) — ответил адресат, показываем участливость при хорошем ответе
    - ("other", None, "") — ответил не адресат (в группе), ведём себя как обычно, без участливости
    - (None, None, "") — не ответ на вопрос дня
    """
    data = qod_tracking.load()
    replier_id = message.from_user.id if message.from_user else None
    if not replier_id:
        return None, None, ""
    chat_id = message.chat.id if message.chat else 0
    by_reply = data.get("by_reply", {})
    by_private = data.get("by_user_private", {})
    today = date.today().isoformat()
    # Ответ по reply_to_message (в группе)
    reply_to = message.reply_to_message
    if reply_to and reply_to.from_user and reply_to.from_user.is_bot:
        key = f"{chat_id}_{reply_to.message_id}"
        entry = by_reply.get(key)
        if entry:
            addressee_id = entry.get("user_id")
            if addressee_id == replier_id:
                return "addressee", replier_id, entry.get("question", "")
            # Ответил не адресат — без участливости
            return "other", None, ""
    # Личка: только адресат может писать, вопрос задавали сегодня
    if chat_id == replier_id:
        entry = by_private.get(str(replier_id))
        if entry and entry.get("sent_at") == today:
            key = f"{replier_id}_{entry.get('message_id')}"
            return "addressee", replier_id, by_reply.get(key, {}).get("question", "Как прошёл день?")
    return None, None, ""


async def _send_question_of_day_to_user(bot: Bot, user_id: int, display_name: str, loop: asyncio.AbstractEventLoop) -> bool:
    """Отправляет «вопрос дня» пользователю. Возвращает True при успехе. Без сообщений за день — не отправляем."""
    try:
        messages = await loop.run_in_executor(None, lambda: user_stats.get_user_messages_for_today(user_id))
        if not messages:
            logger.warning("Вопрос дня: нет сообщений за день у пользователя %s (пропуск)", user_id)
            return False
        question = await loop.run_in_executor(
            None, lambda: generate_question_of_day(messages, display_name or str(user_id))
        )
        u = user_stats.get_user(user_id)
        dest = u.get("question_of_day_destination") or "chat"
        if dest == "chat":
            chat_id = await loop.run_in_executor(None, lambda: user_stats.get_chat_for_question_of_day(user_id))
            if chat_id is None:
                logger.warning("Вопрос дня: нет чата для пользователя %s (пропуск)", user_id)
                return False
            mention = f'<a href="tg://user?id={user_id}">{escape(display_name or str(user_id))}</a>'
            text = f"{mention}, {question}"
        else:
            chat_id = user_id
            text = question
        parse_mode = "HTML" if "<a href=" in text else None
        sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        if sent and sent.message_id:
            qod_tracking.add(chat_id, sent.message_id, user_id, question)
        user_stats.mark_question_of_day_asked(user_id)
        logger.info("Вопрос дня отправлен пользователю %s (%s): %s", user_id, "чат" if dest == "chat" else "личка", question[:50])
        return True
    except Exception as e:
        logger.warning("Не удалось отправить вопрос дня пользователю %s: %s", user_id, e)
        return False


async def _question_of_day_scheduler(bot: Bot) -> None:
    """Вечером (20:00–22:00) рассылает «вопрос дня» пользователям с включённой опцией. Не всем сразу."""
    global _question_of_day_last_sent_at
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        if QUESTION_OF_DAY_SEND_PATH.exists():
            try:
                data = json.loads(QUESTION_OF_DAY_SEND_PATH.read_text(encoding="utf-8"))
                uid = int(data.get("user_id", 0))
                if uid:
                    u = user_stats.get_user(uid)
                    display_name = u.get("display_name") or str(uid)
                    try:
                        QUESTION_OF_DAY_SEND_PATH.unlink()
                    except OSError:
                        pass
                    if await _send_question_of_day_to_user(bot, uid, display_name, loop):
                        _question_of_day_last_sent_at = time.time()
            except Exception as e:
                logger.warning("Ошибка при обработке send-now: %s", e)
                try:
                    QUESTION_OF_DAY_SEND_PATH.unlink()
                except OSError:
                    pass
            continue
        start_h = bot_settings.get_int("question_of_day_start_hour", lo=0, hi=23)
        end_h = bot_settings.get_int("question_of_day_end_hour", lo=0, hi=23)
        if not (start_h <= now.hour < end_h):
            continue
        min_interval = bot_settings.get_int("question_of_day_min_interval_sec", lo=60, hi=600)
        if time.time() - _question_of_day_last_sent_at < min_interval:
            continue
        if not bot_settings.get("question_of_day"):
            continue
        users = user_stats.get_users_for_question_of_day()
        if not users:
            continue
        user_id, display_name = random.choice(users)
        if await _send_question_of_day_to_user(bot, user_id, display_name, loop):
            _question_of_day_last_sent_at = time.time()
            jitter = random.randint(180, 480)
            await asyncio.sleep(jitter)


async def main() -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or "вставьте" in token.lower() or "your_" in token.lower():
        raise ValueError("В .env укажите TELEGRAM_BOT_TOKEN (от @BotFather).")
    if ":" not in token or len(token) < 40:
        raise ValueError("TELEGRAM_BOT_TOKEN похож на неверный (формат: 123456789:AAH...).")

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_ranks, Command("ranks"))
    dp.message.register(cmd_stats, Command("stats"))
    dp.message.register(on_bot_added_to_chat, F.new_chat_members)
    dp.message.register(on_message_to_bot, F.text | F.caption | F.photo, IsDirectedAtBotFilter())
    dp.message.register(check_and_reply, F.text | F.caption | F.photo)

    # Устанавливаем аватарку бота (Путин), если файл есть
    if AVATAR_PATH.is_file():
        try:
            await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(AVATAR_PATH)))
            logger.info("Аватарка бота обновлена из %s", AVATAR_PATH.name)
        except Exception as e:
            logger.warning("Не удалось установить аватарку: %s", e)
    else:
        logger.info("Файл аватарки не найден: %s — положите putin_avatar.jpg в папку проекта", AVATAR_PATH)

    # Маркер времени запуска (для админ-панели: проверка перезапуска)
    _BOT_START_FILE = Path(__file__).resolve().parent / "bot_last_start.json"
    try:
        _BOT_START_FILE.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except OSError:
        pass

    logger.info("Бот запущен. ИИ: %s", os.getenv("OPENAI_BASE_URL", "(не задан)"))
    _debug_log("SESSION_START", detail=f"ИИ={os.getenv('OPENAI_BASE_URL', '—')}")
    asyncio.create_task(restart_checker(RESTART_FLAG_PATH, logger))
    asyncio.create_task(_question_of_day_scheduler(bot))
    asyncio.create_task(social_graph_daily_task(social_graph.process_pending_days, logger))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
