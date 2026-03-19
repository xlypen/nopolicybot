"""IMG-3: Хранение и управление сгенерированными портретами."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PersonalityPortraitRow

PORTRAITS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "portraits"


def _ensure_dir(chat_id: int, user_id: int) -> Path:
    d = PORTRAITS_DIR / str(chat_id) / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_portrait_file(
    chat_id: int,
    user_id: int,
    image_bytes: bytes,
    timestamp: str | None = None,
) -> str:
    """Save image bytes to disk. Returns relative path from project root."""
    ts = timestamp or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    d = _ensure_dir(chat_id, user_id)
    filename = f"{ts}.png"
    path = d / filename
    path.write_bytes(image_bytes)
    return str(path.relative_to(PORTRAITS_DIR.parent.parent))


def compute_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


async def save_portrait_record(
    session: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    profile_id: int | None,
    model_used: str,
    prompt_used: str,
    seed_description: str,
    generation_time_sec: float,
    image_path: str,
    image_hash: str,
    style_variant: str = "concept_art",
) -> int:
    """Save portrait metadata to DB. Returns row id."""
    row = PersonalityPortraitRow(
        user_id=user_id,
        chat_id=chat_id,
        profile_id=profile_id,
        generated_at=datetime.utcnow(),
        model_used=model_used,
        prompt_used=prompt_used,
        seed_description=seed_description,
        generation_time_sec=generation_time_sec,
        image_path=image_path,
        image_hash=image_hash,
        style_variant=style_variant,
    )
    session.add(row)
    await session.flush()
    return row.id


async def get_latest_portrait(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
) -> dict[str, Any] | None:
    """Get latest portrait record for user in chat."""
    stmt = (
        select(PersonalityPortraitRow)
        .where(
            PersonalityPortraitRow.user_id == user_id,
            PersonalityPortraitRow.chat_id == chat_id,
        )
        .order_by(PersonalityPortraitRow.generated_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None
    return _row_to_dict(row)


async def get_portrait_history(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Get portrait history for user in chat."""
    stmt = (
        select(PersonalityPortraitRow)
        .where(
            PersonalityPortraitRow.user_id == user_id,
            PersonalityPortraitRow.chat_id == chat_id,
        )
        .order_by(PersonalityPortraitRow.generated_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [_row_to_dict(r) for r in result.scalars().all()]


async def get_portrait_by_id(
    session: AsyncSession,
    portrait_id: int,
) -> dict[str, Any] | None:
    stmt = select(PersonalityPortraitRow).where(PersonalityPortraitRow.id == portrait_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None
    return _row_to_dict(row)


def _row_to_dict(row: PersonalityPortraitRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "chat_id": row.chat_id,
        "profile_id": row.profile_id,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "model_used": row.model_used,
        "prompt_used": row.prompt_used,
        "seed_description": row.seed_description,
        "generation_time_sec": row.generation_time_sec,
        "image_path": row.image_path,
        "image_hash": row.image_hash,
        "style_variant": row.style_variant,
    }
