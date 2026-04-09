import asyncio
import random
import re

from aiogram import Bot
from aiogram.types import ReactionTypeEmoji

# Список: https://core.telegram.org/bots/api#reactiontypeemoji
TELEGRAM_REACTION_EMOJI: frozenset[str] = frozenset(
    "👍 👎 ❤ 🔥 🎉 🤩 😱 😁 😢 💩 🤮 🥰 🤯 🤔 🤬 👏".split()
)

_VS16 = "\ufe0f"
_ZWJ = "\u200d"


def strip_common_emoji_modifiers(s: str) -> str:
    """Убирает VS16, ZWJ и тон кожи — модели часто возвращают ❤️ вместо ❤, из‑за чего сравнение с allowed ломалось."""
    s = (s or "").strip().replace(_VS16, "").replace(_ZWJ, "")
    return re.sub(r"[\U0001F3FB-\U0001F3FF]", "", s)


def match_allowed_emoji(
    raw: str | None, allowed: set[str] | frozenset[str]
) -> str | None:
    """Возвращает эмодзи из allowed, если строка (после нормализации) совпадает или начинается с одного из набора."""
    if not raw:
        return None
    t = strip_common_emoji_modifiers(raw)
    if t in allowed:
        return t
    for ch in t:
        if ch in allowed:
            return ch
    return None


def sanitize_reaction_emoji(emoji: str | None, allowed: set[str] | frozenset[str], fallback: str = "👍") -> str:
    """Возвращает эмодзи, разрешённый Telegram для реакций, иначе fallback."""
    m = match_allowed_emoji(emoji, allowed)
    return m if m is not None else fallback


def pick_allowed_emoji(
    candidates: list[str], allowed: set[str] | frozenset[str], fallback: str = "👍"
) -> str:
    """Выбирает случайный эмодзи из списка, оставляя только разрешённые Telegram."""
    valid: list[str] = []
    for e in candidates:
        m = match_allowed_emoji(e, allowed)
        if m is not None:
            valid.append(m)
    return random.choice(valid) if valid else fallback


async def set_photo_reaction(
    bot: Bot,
    chat_id: int,
    message_id: int,
    emoji: str,
    *,
    allowed: set[str] | frozenset[str],
    logger,
    debug_log=None,
) -> None:
    """Ставит контекстную реакцию на фото с небольшой задержкой."""
    emoji = sanitize_reaction_emoji(emoji, allowed)
    await asyncio.sleep(random.uniform(1.0, 3.0))
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
        logger.info("Чат %s: реакция на фото %s", chat_id, emoji)
        if debug_log is not None:
            debug_log("PHOTO_REACTION", chat_id=chat_id, detail=emoji)
    except Exception as e:
        if "REACTION_INVALID" in str(e):
            try:
                await bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=[ReactionTypeEmoji(emoji="👍")],
                )
                logger.info("Чат %s: реакция на фото (fallback 👍): %s", chat_id, e)
            except Exception:
                logger.warning("Реакция на фото не поставлена: %s", e)
        else:
            logger.warning("Реакция на фото не поставлена: %s", e)
