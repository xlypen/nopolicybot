"""Storage for structured personality profiles (P-1)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PersonalityProfileRow
from services.personality.schema import PersonalityProfile


async def save_profile(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    profile: PersonalityProfile,
    model_version: str = "v1",
) -> int:
    """Save personality profile to DB. Returns row id."""
    row = PersonalityProfileRow(
        user_id=user_id,
        chat_id=chat_id,
        generated_at=datetime.utcnow(),
        period_days=profile.period_days,
        messages_analyzed=profile.messages_analyzed,
        confidence=profile.confidence,
        profile_json=profile.model_dump(mode="json"),
        model_version=model_version,
    )
    session.add(row)
    await session.flush()
    return row.id


async def get_latest_profile(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
) -> PersonalityProfile | None:
    """Get latest profile for user in chat."""
    stmt = (
        select(PersonalityProfileRow)
        .where(
            PersonalityProfileRow.user_id == user_id,
            PersonalityProfileRow.chat_id == chat_id,
        )
        .order_by(PersonalityProfileRow.generated_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None
    return PersonalityProfile.model_validate(row.profile_json)


async def get_profile_history(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    limit: int = 10,
) -> list[PersonalityProfile]:
    """Get profile history for drift analysis."""
    stmt = (
        select(PersonalityProfileRow)
        .where(
            PersonalityProfileRow.user_id == user_id,
            PersonalityProfileRow.chat_id == chat_id,
        )
        .order_by(PersonalityProfileRow.generated_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [PersonalityProfile.model_validate(r.profile_json) for r in rows]


async def get_profiles_for_chat(
    session: AsyncSession,
    chat_id: int,
    limit_per_user: int = 1,
) -> list[tuple[int, PersonalityProfile]]:
    """
    Get latest profile per user in chat.
    Returns list of (user_id, profile).
    """
    stmt = (
        select(PersonalityProfileRow)
        .where(PersonalityProfileRow.chat_id == chat_id)
        .order_by(PersonalityProfileRow.generated_at.desc())
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    seen: set[int] = set()
    out: list[tuple[int, PersonalityProfile]] = []
    for r in rows:
        if r.user_id in seen:
            continue
        seen.add(r.user_id)
        out.append((r.user_id, PersonalityProfile.model_validate(r.profile_json)))
    return out
