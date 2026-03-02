"""
Telegram-бот: следит за диалогом и делает замечания при политических темах.
ИИ определяет контекст: политика/война + тональность (позитив к президенту РФ / негатив / нейтраль).
"""

import asyncio
import os
import re
import logging
import random
import time
from datetime import date
from html import escape
from pathlib import Path
from collections import deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, BaseFilter
from aiogram.types import Message, InputProfilePhotoStatic
from aiogram.types import FSInputFile
from dotenv import load_dotenv

from ai_analyzer import (
    analyze_messages,
    analyze_batch_style,
    generate_rude_reply,
    generate_kind_reply,
    generate_technical_reply,
    is_technical_question,
)
from openai import APIStatusError
import user_stats


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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CHAT_HISTORY: dict[int, deque[tuple[str, str]]] = {}
HISTORY_SIZE = 50  # храним больше для пачки из 20 в нейросеть
BATCH_SIZE = 20
MSGS_BEFORE_REACT = 5  # пропускаем 5 полит. сообщений, потом включаемся
PATIENCE_PHRASE = "Я долго терпел, но терпение закончилось."
BATCH_STYLE_CACHE_SEC = 300  # обновлять стиль не чаще раза в 5 мин

# Путь к фото для аватарки бота (положите сюда putin_avatar.jpg)
AVATAR_PATH = Path(__file__).resolve().parent / "putin_avatar.jpg"

# Приветствие при добавлении бота в чат
GREETING = "Привет, котятки! Пришёл смотреть за вашим поведением."

API_MIN_INTERVAL = 12
KEYWORD_CHECK_DELAY = 0.5
_chat_last_analysis: dict[int, float] = {}
_chat_scheduled: dict[int, asyncio.Task] = {}
_chat_warning_count: dict[int, int] = {}
_chat_messages_since_political: dict[int, int] = {}
_chat_political_count: dict[int, int] = {}  # счётчик полит. сообщений до первого замечания
_chat_style: dict[int, str] = {}  # "moderate" | "active" | "beast"
_chat_style_updated_at: dict[int, float] = {}
_chat_first_remark_done: dict[int, bool] = {}  # уже сказали "я долго терпел..."
_chat_last_praise_date: dict[int, str] = {}  # "YYYY-MM-DD" для умеренного стиля (1 раз в день)
RESET_AFTER_NEUTRAL_MSGS = 25

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
    """Сообщение обращено к боту: ответ на бота или упоминание @username бота."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        if not message.text and not message.caption:
            return False
        me = await bot.get_me()
        if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot:
            return True
        text = (message.text or message.caption or "").lower()
        return me.username and f"@{me.username}".lower() in text


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


def add_to_history(chat_id: int, user_name: str, text: str) -> None:
    if chat_id not in CHAT_HISTORY:
        CHAT_HISTORY[chat_id] = deque(maxlen=HISTORY_SIZE)
    CHAT_HISTORY[chat_id].append((user_name, text))


FRIENDLY_KEYWORDS = [
    "привет", "здравствуй", "спасибо", "благодар", "молодец", "класс", "круто", "отлично",
    "здорово", "уважаю", "респект", "добрый", "хороший", "супер", "красава", "красавчик",
    "умничка", "умница", "зачёт", "топ", "огонь", "огоньчик",
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


def _reply_text_to_html(text: str) -> str:
    """
    Конвертирует markdown-блоки ``` в HTML для Telegram (parse_mode=HTML).
    Код внутри блоков — в <pre><code>...</code></pre>, остальной текст — escaped.
    """
    if not text or not text.strip():
        return ""
    result = []
    last_end = 0
    # Блоки ```lang?\n...\n```
    for m in re.finditer(r"```(\w*)\n(.*?)```", text, re.DOTALL):
        before = text[last_end : m.start()]
        if before:
            result.append(escape(before))
        code = m.group(2).rstrip()
        code_escaped = escape(code)
        result.append(f"<pre><code>{code_escaped}</code></pre>")
        last_end = m.end()
    if last_end < len(text):
        result.append(escape(text[last_end:]))
    return "".join(result)


def _get_history_lines(chat_id: int) -> list[tuple[str, str]]:
    """Последние сообщения чата для пачки в ИИ."""
    if chat_id not in CHAT_HISTORY or not CHAT_HISTORY[chat_id]:
        return []
    return list(CHAT_HISTORY[chat_id])


async def _run_batch_analysis(
    bot: Bot,
    chat_id: int,
    reply_to_message_id: int,
    initiator_name: str = "Заводила",
    initiator_user_id: int | None = None,
    initiator_message_text: str = "",
) -> None:
    _chat_scheduled.pop(chat_id, None)
    _chat_last_analysis[chat_id] = time.monotonic()

    context = get_recent_context(chat_id)
    if len(context) < 15:
        return

    political_count = _chat_political_count.get(chat_id, 0)
    if political_count < MSGS_BEFORE_REACT:
        return

    lines = _get_history_lines(chat_id)
    # Обновляем стиль по пачке из 20 сообщений (не чаще раза в BATCH_STYLE_CACHE_SEC)
    now = time.monotonic()
    style = _chat_style.get(chat_id, "active")
    if len(lines) >= BATCH_SIZE and (chat_id not in _chat_style_updated_at or now - _chat_style_updated_at[chat_id] > BATCH_STYLE_CACHE_SEC):
        try:
            loop = asyncio.get_event_loop()
            style, batch_political, batch_sentiment = await loop.run_in_executor(
                None, lambda: analyze_batch_style(context)
            )
            _chat_style[chat_id] = style
            _chat_style_updated_at[chat_id] = now
            logger.info("Чат %s: стиль по пачке = %s", chat_id, style)
            # Умеренный + в пачке нет политики → похвала 1 раз в день
            if style == "moderate" and not batch_political:
                today = date.today().isoformat()
                if _chat_last_praise_date.get(chat_id) != today:
                    _chat_last_praise_date[chat_id] = today
                    try:
                        msg = random.choice(NO_POLITICS_PRAISE)
                        await bot.send_message(chat_id=chat_id, text=msg)
                        logger.info("Чат %s: похвала «без политики» (1 раз в день)", chat_id)
                    except Exception as e:
                        logger.exception("Похвала: %s", e)
                return
            if style == "moderate":
                return  # в умеренном не делаем замечаний
        except APIStatusError as e:
            if e.status_code == 402:
                logger.warning("ИИ недоступен: 402")
            else:
                logger.exception("Ошибка API ИИ (batch style): %s", e)
        except Exception as e:
            logger.exception("Ошибка batch_style: %s", e)
        style = _chat_style.get(chat_id, "active")

    if style == "moderate":
        return

    # Активный: замечание через раз (5-е, 7-е, 9-е...). Зверь: на каждое после 5-го.
    if style == "active" and (political_count - MSGS_BEFORE_REACT) % 2 != 0:
        return

    # Уточняем по контексту: политика ли и тональность (чтобы не ругать за позитив к президенту)
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

    if initiator_user_id is not None:
        user_stats.record_message(
            initiator_user_id,
            initiator_message_text[:500],
            sentiment,
            is_political,
            initiator_name,
        )

    if not is_political:
        _chat_messages_since_political[chat_id] = _chat_messages_since_political.get(chat_id, 0) + 1
        if _chat_messages_since_political[chat_id] >= RESET_AFTER_NEUTRAL_MSGS:
            _chat_warning_count[chat_id] = 0
            _chat_messages_since_political[chat_id] = 0
            _chat_political_count[chat_id] = 0
            _chat_first_remark_done[chat_id] = False
        return

    if sentiment == "positive":
        try:
            msg = random.choice(ENCOURAGE_LOYAL).format(name=initiator_name)
            await bot.send_message(chat_id=chat_id, text=msg, reply_to_message_id=reply_to_message_id)
            logger.info("Чат %s: поощрение (позитив к президенту РФ)", chat_id)
        except Exception as e:
            logger.exception("Поощрение: %s", e)
        return

    _chat_messages_since_political[chat_id] = 0
    level = _chat_warning_count.get(chat_id, 0)
    _chat_warning_count[chat_id] = level + 1

    insult = _insult_by_level(level, initiator_name)
    article = _random_article_line(initiator_name)
    body = f"{POLITICS_LINE}\n{insult}\n{article}"
    if not _chat_first_remark_done.get(chat_id, False):
        body = f"{PATIENCE_PHRASE}\n{body}"
        _chat_first_remark_done[chat_id] = True

    try:
        await bot.send_message(chat_id=chat_id, text=body, reply_to_message_id=reply_to_message_id)
        if initiator_user_id is not None:
            user_stats.record_warning(initiator_user_id)
        logger.info("Замечание в чат %s (уровень %s, стиль %s)", chat_id, level, style)
    except Exception as e:
        logger.exception("Не удалось отправить замечание: %s", e)


async def check_and_reply(message: Message) -> None:
    text = (message.text or message.caption or "").strip()
    if not text:
        return

    user_name = message.from_user.username or message.from_user.first_name or "Участник"
    first_name = _safe_name((message.from_user.first_name or message.from_user.username or "Участник"))
    chat_id = message.chat.id
    add_to_history(chat_id, user_name, text)

    reply_to = message.reply_to_message
    reply_text = (reply_to.text or reply_to.caption or "") if reply_to else ""
    msg_has_keyword = contains_political_keyword(text)
    reply_has_keyword = contains_political_keyword(reply_text) if reply_text else False

    # Считаем «политическое событие»: само сообщение с полит. темой или ответ в полит. тред
    is_political_event = msg_has_keyword or (reply_to and reply_has_keyword and not (reply_to.from_user and reply_to.from_user.is_bot))
    if is_political_event:
        _chat_political_count[chat_id] = _chat_political_count.get(chat_id, 0) + 1
        # Сразу заводим запись в базе участников, чтобы user_stats.json не оставался пустым
        if message.from_user:
            user_stats.get_user(message.from_user.id, first_name)

    # Реакция только после 5 полит. сообщений (таймаут)
    if _chat_political_count.get(chat_id, 0) < MSGS_BEFORE_REACT:
        return
    if chat_id in _chat_scheduled:
        return
    now = time.monotonic()
    if now - _chat_last_analysis.get(chat_id, 0) < API_MIN_INTERVAL:
        return

    bot = message.bot
    msg_id = message.message_id
    initiator_name = first_name
    initiator_user_id = message.from_user.id if message.from_user else None

    logger.info("Чат %s: полит. событие №%s, запуск анализа", chat_id, _chat_political_count[chat_id])

    async def scheduled() -> None:
        try:
            await asyncio.sleep(KEYWORD_CHECK_DELAY)
            await _run_batch_analysis(
                bot, chat_id, msg_id, initiator_name,
                initiator_user_id=initiator_user_id,
                initiator_message_text=text,
            )
        except Exception as e:
            logger.exception("Ошибка в отложенной проверке: %s", e)

    _chat_scheduled[chat_id] = asyncio.create_task(scheduled())


async def on_bot_added_to_chat(message: Message) -> None:
    """Приветствие при добавлении бота в чат (первый «логин»)."""
    bot = message.bot
    me = await bot.get_me()
    if not message.new_chat_members:
        return
    if any(m.id == me.id for m in message.new_chat_members):
        try:
            await message.reply(GREETING)
            logger.info("Чат %s: бот добавлен, отправлено приветствие", message.chat.id)
        except Exception as e:
            logger.exception("Не удалось отправить приветствие: %s", e)


async def on_message_to_bot(message: Message) -> None:
    """Ответ на обращение к боту: язвительный/грубый ответ через нейросеть."""
    if message.from_user and message.from_user.is_bot:
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        return
    user_name = message.from_user.username or message.from_user.first_name or "Участник"
    first_name = _safe_name((message.from_user.first_name or message.from_user.username or "Участник"))
    chat_id = message.chat.id
    add_to_history(chat_id, user_name, text)

    # Тег пользователя, чтобы он получил уведомление (tg://user?id=...)
    user_id = message.from_user.id
    mention = f'<a href="tg://user?id={user_id}">{escape(first_name)}</a>'

    context = get_recent_context(chat_id)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: user_stats.record_message_to_bot(user_id, text, first_name),
        )
        # Проверяем: доброе обращение или похвала президенту — отвечаем по-доброму и + в карму
        context_for_analysis = (context or "") + "\n" + f"{first_name}: {text}"
        is_political, _, sentiment = await loop.run_in_executor(
            None, lambda: analyze_messages(context_for_analysis)
        )
        # Похвала — только при прямой связи «президент РФ/Путин/Россия + похвала». Доброе обращение без политики — тоже ок.
        is_positive = (
            sentiment == "positive"  # строгая проверка в analyze_messages
            or (not contains_political_keyword(text) and is_likely_friendly(text))
        )
        if is_positive:
            user_stats.record_message(user_id, text, "positive", is_political or True, first_name)
            reply_text = await loop.run_in_executor(
                None,
                lambda: generate_kind_reply(context or "(нет контекста)", text, first_name),
            )
        elif is_technical_question(text):
            reply_text = await loop.run_in_executor(
                None,
                lambda: generate_technical_reply(context or "(нет контекста)", text, first_name),
            )
        else:
            portrait = await loop.run_in_executor(
                None,
                lambda: user_stats.get_portrait_for_reply(message.from_user.id, first_name),
            )
            use_yesterday = random.random() < 0.01
            yesterday_quotes = (
                await loop.run_in_executor(None, lambda: user_stats.get_yesterday_quotes(user_id))
                if use_yesterday else []
            )
            reply_text = await loop.run_in_executor(
                None,
                lambda ctx=context or "(нет контекста)", pt=portrait or "", yq=yesterday_quotes: generate_rude_reply(
                    ctx, text, first_name, user_portrait=pt, yesterday_quotes=yq if yq else None
                ),
            )
        # Убираем дубль имени/ника из начала ответа — в сообщении только тег (упоминание)
        reply_clean = reply_text.strip()
        names_to_strip = [first_name]
        if user_name and user_name != first_name:
            names_to_strip.append(user_name)
        while True:
            changed = False
            for name in names_to_strip:
                for sep in (",", "!", " ", ":", "，"):
                    prefix = name + sep
                    if reply_clean.lower().startswith(prefix.lower()):
                        reply_clean = reply_clean[len(prefix):].strip()
                        changed = True
                        break
                if changed:
                    break
            if not changed:
                break
        if reply_clean and reply_clean[0].isupper():
            reply_clean = reply_clean[0].lower() + reply_clean[1:]
        body_html = _reply_text_to_html(reply_clean) if reply_clean else ""
        text_with_mention = f"{mention}, {body_html}" if body_html else mention
        await message.reply(text_with_mention, parse_mode="HTML")
        logger.info("Чат %s: ответ пользователю %s", chat_id, first_name)
    except APIStatusError as e:
        if e.status_code == 402:
            logger.warning("ИИ недоступен: 402")
        else:
            logger.exception("Ошибка API ИИ при ответе: %s", e)
        await message.reply(f"{mention}, сейчас не в настроении, напиши потом.", parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка при генерации ответа: %s", e)
        await message.reply(f"{mention}, отвали, некогда.", parse_mode="HTML")


async def cmd_start(message: Message) -> None:
    await message.reply(
        "Привет! Я слежу за темой разговора в этом чате.\n"
        "Политика и война — под запретом (но похвалить президента РФ можно 🇷🇺).\n\n"
        "Добавьте меня в группу и дайте право читать сообщения."
    )


async def cmd_ranks(message: Message) -> None:
    """Команда /ranks — выводит ранги участников в чат."""
    text = user_stats.get_ranks_for_chat()
    await message.reply(text, parse_mode="HTML")


async def cmd_stats(message: Message) -> None:
    """Команда /stats — выводит статистику по пользователям в лог и подсказывает, где база."""
    stats_text = user_stats.get_stats_for_log()
    logger.info("\n%s", stats_text)
    base_path = user_stats.USERS_JSON
    await message.reply(
        "Статистика записана в лог (консоль или файл, куда пишет бот).\n\n"
        f"База участников (ранг, портрет, счётчики):\n<code>{base_path}</code>\n\n"
        "Файл не обнуляется при перезапуске. Он заполняется, когда кто-то пишет боту или в чате появляются полит. сообщения (после этого добавляются записи и счётчики).",
        parse_mode="HTML",
    )


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
    dp.message.register(on_message_to_bot, F.text | F.caption, IsDirectedAtBotFilter())
    dp.message.register(check_and_reply, F.text | F.caption)

    # Устанавливаем аватарку бота (Путин), если файл есть
    if AVATAR_PATH.is_file():
        try:
            await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(AVATAR_PATH)))
            logger.info("Аватарка бота обновлена из %s", AVATAR_PATH.name)
        except Exception as e:
            logger.warning("Не удалось установить аватарку: %s", e)
    else:
        logger.info("Файл аватарки не найден: %s — положите putin_avatar.jpg в папку проекта", AVATAR_PATH)

    logger.info("Бот запущен. ИИ: %s", os.getenv("OPENAI_BASE_URL", "(не задан)"))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
