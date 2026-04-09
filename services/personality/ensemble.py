"""Ensemble of models for personality profile (P-3)."""

import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean, stdev

from services.personality.builder import build_structured_profile_from_messages
from services.personality.contextual import enrich_profile_with_context
from services.personality.model_config import get_ensemble_models
from services.personality.schema import (
    OCEAN_KEYS,
    CommunicationProfile,
    DarkTriad,
    DarkTriadTrait,
    EmotionalProfile,
    EnsembleStats,
    OceanTraits,
    PersonalityProfile,
    TopicsProfile,
)

logger = logging.getLogger(__name__)
LOW_AGREEMENT_THRESHOLD = 0.25


def _aggregate_ocean(profiles: list[PersonalityProfile]) -> tuple[OceanTraits, dict[str, float], list[str]]:
    """Aggregate OCEAN: mean per dimension, return (OceanTraits, std_by_dim, low_agreement_dims)."""
    values = {k: [] for k in OCEAN_KEYS}
    for p in profiles:
        for k in OCEAN_KEYS:
            values[k].append(getattr(p.ocean, k))

    means = {}
    stds = {}
    low_agreement = []
    for k in OCEAN_KEYS:
        vals = values[k]
        means[k] = mean(vals)
        stds[k] = stdev(vals) if len(vals) > 1 else 0.0
        if stds[k] > LOW_AGREEMENT_THRESHOLD:
            low_agreement.append(k)

    return OceanTraits(**means), stds, low_agreement


def _aggregate_dark_triad(profiles: list[PersonalityProfile]) -> DarkTriad:
    """Aggregate Dark Triad: mean score per dimension, most common label."""
    def agg_trait(key: str) -> DarkTriadTrait:
        scores = [getattr(getattr(p.dark_triad, key), "score") for p in profiles]
        labels = [getattr(getattr(p.dark_triad, key), "label") for p in profiles]
        avg_score = mean(scores)
        label = max(set(labels), key=labels.count) if labels else "low"
        return DarkTriadTrait(label=label, score=round(avg_score, 3))

    return DarkTriad(
        narcissism=agg_trait("narcissism"),
        machiavellianism=agg_trait("machiavellianism"),
        psychopathy=agg_trait("psychopathy"),
    )


def _aggregate_communication(profiles: list[PersonalityProfile]) -> CommunicationProfile:
    """Aggregate communication: mean of numeric fields, most common style."""
    styles = [p.communication.style for p in profiles]
    style = max(set(styles), key=styles.count) if styles else "assertive"
    return CommunicationProfile(
        style=style,
        conflict_tendency=mean([p.communication.conflict_tendency for p in profiles]),
        influence_seeking=mean([p.communication.influence_seeking for p in profiles]),
        emotional_expressiveness=mean([p.communication.emotional_expressiveness for p in profiles]),
        topic_consistency=mean([p.communication.topic_consistency for p in profiles]),
    )


def _aggregate_emotional(profiles: list[PersonalityProfile]) -> EmotionalProfile:
    """Aggregate emotional profile."""
    all_emotions = []
    for p in profiles:
        all_emotions.extend(p.emotional_profile.dominant_emotions)
    top_emotions = list(dict.fromkeys(all_emotions))[:5] if all_emotions else []
    return EmotionalProfile(
        valence=mean([p.emotional_profile.valence for p in profiles]),
        arousal=mean([p.emotional_profile.arousal for p in profiles]),
        dominant_emotions=top_emotions,
    )


def _aggregate_topics(profiles: list[PersonalityProfile]) -> TopicsProfile:
    """Aggregate topics: union of primary/secondary/avoided from all profiles."""
    primary = []
    secondary = []
    avoided = []
    for p in profiles:
        primary.extend(p.topics.primary)
        secondary.extend(p.topics.secondary)
        avoided.extend(p.topics.avoided)
    return TopicsProfile(
        primary=list(dict.fromkeys(primary)),
        secondary=list(dict.fromkeys(secondary)),
        avoided=list(dict.fromkeys(avoided)),
    )


def build_ensemble_profile(
    messages: list[dict],
    user_id: str | int,
    username: str = "",
    period_days: int = 30,
    chat_description: str = "Telegram chat",
    models: list[str] | None = None,
    min_models: int = 2,
    diagnostics: list[str] | None = None,
) -> PersonalityProfile | None:
    """
    Build profile via ensemble of N models. Aggregates OCEAN (mean), computes agreement_score.
    Returns PersonalityProfile with ensemble_stats, or None if fewer than min_models succeed.
    If diagnostics is a list, append human-readable failure/success notes (for API/UI).
    """
    def note(msg: str) -> None:
        if diagnostics is not None:
            diagnostics.append(msg)

    models = models or get_ensemble_models()
    if (os.getenv("PERSONALITY_SINGLE_MODEL_BUILD") or "").strip().lower() in ("1", "true", "yes"):
        models = models[:1]
        note("PERSONALITY_SINGLE_MODEL_BUILD=1 — одна модель (меньше запросов к OpenRouter)")

    if not models:
        fb = (os.getenv("OPENAI_MODEL") or os.getenv("PERSONALITY_FALLBACK_MODEL") or "gpt-4o-mini").strip()
        note(f"PERSONALITY_ENSEMBLE_MODELS пусто — одна модель: {fb}")
        models = [fb]

    profiles: list[PersonalityProfile] = []
    models_used: list[str] = []

    def _build(model: str) -> tuple[str, PersonalityProfile | None]:
        p = build_structured_profile_from_messages(
            messages=messages,
            user_id=user_id,
            username=username,
            period_days=period_days,
            chat_description=chat_description,
            model=model,
            max_retries=1,
            skip_context_enrich=True,
        )
        return (model, p)

    with ThreadPoolExecutor(max_workers=min(len(models), 5)) as ex:
        futures = {ex.submit(_build, m): m for m in models}
        for future in as_completed(futures):
            model, profile = future.result()
            if profile:
                profiles.append(profile)
                models_used.append(model)
            else:
                note(f"модель {model}: нет ответа или ошибка разбора")

    if len(profiles) < min_models:
        logger.warning("Ensemble: only %d/%d models succeeded, need %d", len(profiles), len(models), min_models)
        if profiles:
            base = profiles[0]
            base.ensemble_stats = EnsembleStats(
                models_used=models_used,
                agreement_score=0.0,
                low_agreement_dimensions=list(OCEAN_KEYS),
            )
            return enrich_profile_with_context(base, messages)
        fallback = (os.getenv("PERSONALITY_FALLBACK_MODEL") or os.getenv("OPENAI_MODEL") or "").strip()
        if fallback:
            note(f"ансамбль не дал профилей, повтор с {fallback} (до 3 попыток разбора ответа)")
            solo = build_structured_profile_from_messages(
                messages=messages,
                user_id=user_id,
                username=username,
                period_days=period_days,
                chat_description=chat_description,
                model=fallback,
                max_retries=2,
                skip_context_enrich=True,
            )
            if solo:
                note("портрет собран запасной моделью")
                solo.ensemble_stats = EnsembleStats(
                    models_used=[fallback],
                    agreement_score=0.0,
                    low_agreement_dimensions=list(OCEAN_KEYS),
                )
                return enrich_profile_with_context(solo, messages)
            note("запасная модель не вернула профиль — проверьте ключ, лимиты и имя модели")
        else:
            note(
                "все модели ансамбля не сработали; задайте OPENAI_MODEL или PERSONALITY_FALLBACK_MODEL "
                "или PERSONALITY_ENSEMBLE_MODELS с рабочими именами (OpenRouter / провайдер)"
            )
        return None

    ocean, stds, low_agreement = _aggregate_ocean(profiles)
    mean_std = mean(stds.values()) if stds else 0.0
    agreement_score = max(0.0, min(1.0, 1.0 - mean_std))
    base_confidence = mean([p.confidence for p in profiles])
    final_confidence = base_confidence * agreement_score

    base = profiles[0]
    profile = PersonalityProfile(
        user_id=str(user_id),
        username=username or str(user_id),
        generated_at=base.generated_at or "",
        period_days=period_days,
        messages_analyzed=len(messages),
        confidence=round(final_confidence, 3),
        ocean=ocean,
        dark_triad=_aggregate_dark_triad(profiles),
        communication=_aggregate_communication(profiles),
        emotional_profile=_aggregate_emotional(profiles),
        topics=_aggregate_topics(profiles),
        role_in_community=(Counter(p.role_in_community for p in profiles if p.role_in_community).most_common(1)[0][0] if any(p.role_in_community for p in profiles) else ""),
        summary=base.summary or "",
        ensemble_stats=EnsembleStats(
            models_used=models_used,
            agreement_score=round(agreement_score, 3),
            low_agreement_dimensions=low_agreement,
        ),
    )
    return enrich_profile_with_context(profile, messages)
