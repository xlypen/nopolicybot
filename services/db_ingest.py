from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from db.engine import get_db
from db.repositories.edge_repo import EdgeRepository
from db.repositories.message_repo import MessageRepository
from db.repositories.user_repo import UserRepository
from services.storage_cutover import get_storage_mode, storage_db_writes_enabled

_log = logging.getLogger(__name__)
_CHAT_ID_BITS = 32
_ingest_semaphore = asyncio.Semaphore(1)  # Serialize DB writes (SQLite doesn't like concurrent)
_MESSAGE_ID_BITS = 31
_CHAT_ID_MASK = (1 << _CHAT_ID_BITS) - 1
_MESSAGE_ID_MASK = (1 << _MESSAGE_ID_BITS) - 1


def _combined_telegram_id(chat_id: int, message_id: int) -> int:
    # Keep uniqueness across chats even though Telegram message_id is per-chat.
    # The value must fit signed 64-bit INTEGER (SQLite/PostgreSQL BIGINT).
    # 32 bits (chat) + 31 bits (message) = 63 bits max.
    chat_part = int(chat_id) & _CHAT_ID_MASK
    message_part = int(message_id) & _MESSAGE_ID_MASK
    return (chat_part << _MESSAGE_ID_BITS) | message_part


def _sentiment_to_score(sentiment: str | None) -> float | None:
    raw = (sentiment or "").strip().lower()
    if raw == "positive":
        return 1.0
    if raw == "negative":
        return -1.0
    if raw == "neutral":
        return 0.0
    return None


async def ingest_message_event(
    *,
    chat_id: int,
    user_id: int,
    message_id: int,
    text: str,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    media_type: str = "text",
    replied_to_user_id: int | None = None,
    sentiment: str | None = None,
    is_political: bool = False,
) -> bool:
    mode = get_storage_mode()
    if not storage_db_writes_enabled(mode):
        _log.info("db ingest skip: storage_mode=%s (writes disabled)", mode)
        return False
    if not int(user_id):
        _log.info("db ingest skip: user_id=0")
        return False

    sent_at = datetime.now(tz=timezone.utc)
    tone_score = _sentiment_to_score(sentiment)
    async with _ingest_semaphore:
        async with get_db() as session:
            user_repo = UserRepository(session)
            msg_repo = MessageRepository(session)
            edge_repo = EdgeRepository(session)

            await user_repo.get_or_create(
                int(user_id),
                int(chat_id),
                username=(username or "")[:200],
                first_name=(first_name or "")[:200],
                last_name=(last_name or "")[:200],
                is_active=True,
                last_seen=sent_at,
            )

            telegram_id = _combined_telegram_id(int(chat_id), int(message_id))
            try:
                async with session.begin_nested():
                    await msg_repo.add(
                        telegram_id=telegram_id,
                        chat_id=int(chat_id),
                        user_id=int(user_id),
                        text=(text or "")[:2000],
                        media_type=(media_type or "text")[:80],
                        replied_to=int(replied_to_user_id) if replied_to_user_id else None,
                        sent_at=sent_at,
                        tone_score=tone_score,
                        risk_flags=(["politics"] if is_political else []),
                    )
            except Exception as e:
                _log.warning("db ingest msg_repo.add failed chat=%s msg=%s: %s", chat_id, message_id, e)

            if replied_to_user_id and int(replied_to_user_id) and int(replied_to_user_id) != int(user_id):
                await edge_repo.upsert(
                    chat_id=int(chat_id),
                    from_user=int(user_id),
                    to_user=int(replied_to_user_id),
                    weight_delta=1.0,
                    period="7d",
                )
    _log.debug("db ingest ok chat=%s user=%s msg=%s", chat_id, user_id, message_id)
    return True
