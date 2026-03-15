"""Personality drift — track changes between profiles (P-4)."""

import logging
from statistics import mean

from sqlalchemy.ext.asyncio import AsyncSession

from services.audit_log import write_event
from services.personality.schema import OCEAN_KEYS, PersonalityDrift, PersonalityProfile
from services.personality.storage import get_profile_history

logger = logging.getLogger(__name__)

DRIFT_SCORE_THRESHOLD = 0.25
SINGLE_DELTA_THRESHOLD = 0.20


def _format_delta(dim: str, delta: float) -> str:
    sign = "+" if delta >= 0 else ""
    labels = {
        "openness": "открытость",
        "conscientiousness": "добросовестность",
        "extraversion": "экстраверсия",
        "agreeableness": "доброжелательность",
        "neuroticism": "нейротизм",
    }
    label = labels.get(dim, dim)
    return f"{label} ({sign}{delta:.2f})"


async def calculate_drift(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    emit_alert: bool = True,
) -> PersonalityDrift | None:
    """
    Calculate drift between latest two profiles.
    Returns PersonalityDrift or None if fewer than 2 profiles.
    When alert=True, writes to audit_log.
    """
    history = await get_profile_history(session, user_id, chat_id, limit=2)
    if len(history) < 2:
        return None

    current, previous = history[0], history[1]
    deltas: dict[str, float] = {}
    for k in OCEAN_KEYS:
        deltas[k] = round(getattr(current.ocean, k) - getattr(previous.ocean, k), 3)

    drift_score = mean(abs(d) for d in deltas.values())
    significant = [k for k, d in deltas.items() if abs(d) > SINGLE_DELTA_THRESHOLD]
    alert = drift_score > DRIFT_SCORE_THRESHOLD or any(abs(d) > SINGLE_DELTA_THRESHOLD for d in deltas.values())

    period = f"{previous.generated_at[:10]} — {current.generated_at[:10]}" if previous.generated_at and current.generated_at else ""

    if significant:
        parts = [_format_delta(k, deltas[k]) for k in significant]
        alert_reason = "Резкие изменения: " + ", ".join(parts)
    else:
        alert_reason = ""

    drift = PersonalityDrift(
        user_id=str(user_id),
        chat_id=str(chat_id),
        period=period,
        deltas=deltas,
        significant_changes=significant,
        drift_score=round(drift_score, 3),
        alert=alert,
        alert_reason=alert_reason,
    )

    if alert and emit_alert:
        write_event(
            "personality_drift_alert",
            severity="warning",
            source="personality",
            payload={
                "user_id": user_id,
                "chat_id": chat_id,
                "drift_score": drift.drift_score,
                "significant_changes": significant,
                "alert_reason": alert_reason,
            },
        )
        logger.info("Personality drift alert: user=%s chat=%s score=%.2f changes=%s", user_id, chat_id, drift_score, significant)

    return drift


def calculate_drift_sync(profiles: list[PersonalityProfile], user_id: str = "", chat_id: str = "") -> PersonalityDrift | None:
    """
    Sync variant: compute drift from two profiles (for tests).
    """
    if len(profiles) < 2:
        return None
    current, previous = profiles[0], profiles[1]
    deltas = {k: round(getattr(current.ocean, k) - getattr(previous.ocean, k), 3) for k in OCEAN_KEYS}
    drift_score = mean(abs(d) for d in deltas.values())
    significant = [k for k, d in deltas.items() if abs(d) > SINGLE_DELTA_THRESHOLD]
    alert = drift_score > DRIFT_SCORE_THRESHOLD or bool(significant)
    alert_reason = "Резкие изменения: " + ", ".join(_format_delta(k, deltas[k]) for k in significant) if significant else ""
    return PersonalityDrift(
        user_id=user_id,
        chat_id=chat_id,
        period="",
        deltas=deltas,
        significant_changes=significant,
        drift_score=round(drift_score, 3),
        alert=alert,
        alert_reason=alert_reason,
    )
