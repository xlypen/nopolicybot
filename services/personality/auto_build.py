"""Auto-build personality profiles (P-10) and unified portrait generation (P-11)."""

import logging
import os
import threading
import time

import asyncio

from ai.client import chat_complete_with_fallback, get_client, prefer_free_mode
from services.personality.ensemble import build_ensemble_profile
from services.personality.schema import PersonalityProfile

import user_stats

logger = logging.getLogger(__name__)

BUILD_THRESHOLD = 50
RATE_LIMIT_SECONDS = 24 * 3600

_messages_since_build: dict[str, int] = {}
_last_build_ts: dict[str, float] = {}
_lock = threading.Lock()


def _user_key(user_id: int, chat_id: int) -> str:
    return f"{user_id}:{chat_id}"


def check_and_trigger_build(user_id: int, chat_id: int) -> None:
    """Increment message counter; trigger background profile build at threshold."""
    key = _user_key(user_id, chat_id)
    now = time.time()

    with _lock:
        if now - _last_build_ts.get(key, 0) < RATE_LIMIT_SECONDS:
            _messages_since_build[key] = _messages_since_build.get(key, 0) + 1
            return

        count = _messages_since_build.get(key, 0) + 1
        _messages_since_build[key] = count

        if count < BUILD_THRESHOLD:
            return

        _messages_since_build[key] = 0
        _last_build_ts[key] = now

    logger.info(
        "[P-10] Triggering auto-build for user=%s chat=%s (counter=%s)",
        user_id, chat_id, count,
    )
    t = threading.Thread(
        target=_safe_auto_build,
        args=(user_id, chat_id),
        daemon=True,
    )
    t.start()


def _safe_auto_build(user_id: int, chat_id: int) -> None:
    try:
        auto_build_profile(user_id, chat_id)
    except Exception as e:
        logger.exception("[P-10] auto_build_profile failed for user=%s: %s", user_id, e)


def auto_build_profile(user_id: int, chat_id: int) -> None:
    """Build ensemble profile, save to DB, and generate text portrait."""
    messages = user_stats.get_user_messages_archive(user_id, chat_id=chat_id)
    if not messages:
        logger.info("[P-10] No messages for user=%s chat=%s", user_id, chat_id)
        return

    u = user_stats.get_user(user_id)
    username = u.get("display_name") or str(user_id)

    profile = build_ensemble_profile(
        messages=messages,
        user_id=user_id,
        username=username,
        period_days=30,
    )

    if not profile:
        logger.warning("[P-10] Ensemble returned None for user=%s", user_id)
        return

    _save_profile_sync(user_id, chat_id, profile)
    logger.info(
        "[P-10] Profile saved for user=%s chat=%s (confidence=%.2f, msgs=%s)",
        user_id, chat_id, profile.confidence, profile.messages_analyzed,
    )

    try:
        portrait_text = generate_portrait(profile, messages[-20:], username)
        if portrait_text:
            user_stats.set_deep_portrait(user_id, portrait_text)
            logger.info("[P-11] Portrait updated for user=%s (%d chars)", user_id, len(portrait_text))
    except Exception as e:
        logger.warning("[P-11] Portrait generation failed for user=%s: %s", user_id, e)


def _save_profile_sync(user_id: int, chat_id: int, profile: PersonalityProfile) -> None:
    """Save profile via async storage from a sync/thread context."""
    from db.engine import AsyncSessionLocal
    from services.personality.storage import save_profile

    async def _save() -> None:
        async with AsyncSessionLocal() as session:
            await save_profile(session, user_id, chat_id, profile)
            await session.commit()

    asyncio.run(_save())


def generate_portrait(
    profile: PersonalityProfile,
    recent_messages: list[dict],
    username: str,
) -> str:
    """Generate a 3-paragraph human-readable portrait from structured profile (P-11)."""
    ocean = profile.ocean
    dt = profile.dark_triad
    topics = profile.topics
    comm = profile.communication

    msg_lines = []
    for m in recent_messages[-15:]:
        t = (m.get("text") or "").strip()
        if t:
            msg_lines.append(t[:200])
    messages_text = "\n".join(msg_lines) if msg_lines else "(нет сообщений)"

    prompt = (
        "Ты — психолог-аналитик. Напиши портрет участника чата на основе "
        "его структурного психологического профиля и недавних сообщений.\n\n"
        f"Участник: {username}\n\n"
        "OCEAN (Big Five):\n"
        f"- Открытость: {ocean.openness:.2f}\n"
        f"- Добросовестность: {ocean.conscientiousness:.2f}\n"
        f"- Экстраверсия: {ocean.extraversion:.2f}\n"
        f"- Доброжелательность: {ocean.agreeableness:.2f}\n"
        f"- Нейротизм: {ocean.neuroticism:.2f}\n\n"
        "Тёмная триада:\n"
        f"- Нарциссизм: {dt.narcissism.label} ({dt.narcissism.score:.2f})\n"
        f"- Макиавеллизм: {dt.machiavellianism.label} ({dt.machiavellianism.score:.2f})\n"
        f"- Психопатия: {dt.psychopathy.label} ({dt.psychopathy.score:.2f})\n\n"
        f"Стиль общения: {comm.style}\n"
        f"Склонность к конфликтам: {comm.conflict_tendency:.2f}\n"
        f"Стремление к влиянию: {comm.influence_seeking:.2f}\n\n"
        f"Темы: {', '.join(topics.primary[:5]) if topics.primary else 'не определены'}\n"
        f"Роль в сообществе: {profile.role_in_community or 'не определена'}\n\n"
        f"Последние сообщения:\n{messages_text}\n\n"
        "Напиши 3 абзаца:\n"
        "1. Психологический портрет (характер, мотивация, эмоциональный фон)\n"
        "2. Профессионально-деловой стиль (как ведёт дискуссии, отстаивает позицию, "
        "реагирует на критику)\n"
        "3. Политические и общественные взгляды (на основе тем и сообщений)\n\n"
        "Пиши кратко (до 500 слов), по-русски, в третьем лице."
    )

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    text, _model_used = chat_complete_with_fallback(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.5,
        max_tokens=1000,
        prefer_free=prefer_free_mode(),
    )
    return (text or "").strip()
