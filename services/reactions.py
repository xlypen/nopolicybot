import asyncio
import random

from aiogram import Bot
from aiogram.types import ReactionTypeEmoji


def sanitize_reaction_emoji(emoji: str | None, allowed: set[str] | frozenset[str], fallback: str = "👍") -> str:
    """Возвращает эмодзи, разрешённый Telegram для реакций, иначе fallback."""
    if emoji and emoji.strip() in allowed:
        return emoji.strip()
    return fallback


def pick_allowed_emoji(
    candidates: list[str], allowed: set[str] | frozenset[str], fallback: str = "👍"
) -> str:
    """Выбирает случайный эмодзи из списка, оставляя только разрешённые Telegram."""
    valid = [e for e in candidates if e and e.strip() in allowed]
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
