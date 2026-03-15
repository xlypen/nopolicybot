"""Pydantic schema for structured personality profile (P-1)."""

from typing import Literal

from pydantic import BaseModel, Field

OCEAN_KEYS = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]


class OceanTraits(BaseModel):
    """Big Five (OCEAN) — float 0.0–1.0."""

    openness: float = Field(ge=0, le=1, default=0.5)
    conscientiousness: float = Field(ge=0, le=1, default=0.5)
    extraversion: float = Field(ge=0, le=1, default=0.5)
    agreeableness: float = Field(ge=0, le=1, default=0.5)
    neuroticism: float = Field(ge=0, le=1, default=0.5)


class DarkTriadTrait(BaseModel):
    """Single Dark Triad dimension: label + numeric score."""

    label: Literal["low", "medium", "high"] = "low"
    score: float = Field(ge=0, le=1, default=0.0)


class DarkTriad(BaseModel):
    """Dark Triad: narcissism, machiavellianism, psychopathy."""

    narcissism: DarkTriadTrait = Field(default_factory=lambda: DarkTriadTrait(label="low", score=0.0))
    machiavellianism: DarkTriadTrait = Field(default_factory=lambda: DarkTriadTrait(label="low", score=0.0))
    psychopathy: DarkTriadTrait = Field(default_factory=lambda: DarkTriadTrait(label="low", score=0.0))


class CommunicationProfile(BaseModel):
    """Communication style in chat context."""

    style: Literal["assertive", "passive", "aggressive", "passive-aggressive"] = "assertive"
    conflict_tendency: float = Field(ge=0, le=1, default=0.5)
    influence_seeking: float = Field(ge=0, le=1, default=0.5)
    emotional_expressiveness: float = Field(ge=0, le=1, default=0.5)
    topic_consistency: float = Field(ge=0, le=1, default=0.5)


class EmotionalProfile(BaseModel):
    """Emotional valence and arousal."""

    valence: float = Field(ge=0, le=1, default=0.5)
    arousal: float = Field(ge=0, le=1, default=0.5)
    dominant_emotions: list[str] = Field(default_factory=list)


class TopicsProfile(BaseModel):
    """Topic preferences."""

    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    avoided: list[str] = Field(default_factory=list)


class EnsembleStats(BaseModel):
    """Stats from multi-model ensemble (P-3)."""

    models_used: list[str] = Field(default_factory=list)
    agreement_score: float = Field(ge=0, le=1, default=0.0)
    low_agreement_dimensions: list[str] = Field(default_factory=list)


class PersonalityDrift(BaseModel):
    """Drift between two personality profiles (P-4)."""

    user_id: str = ""
    chat_id: str = ""
    period: str = ""
    deltas: dict[str, float] = Field(default_factory=dict)
    significant_changes: list[str] = Field(default_factory=list)
    drift_score: float = Field(ge=0, le=1, default=0.0)
    alert: bool = False
    alert_reason: str = ""


class PersonalityProfile(BaseModel):
    """Structured personality profile — target schema from P-1."""

    user_id: str = ""
    username: str = ""
    generated_at: str = ""
    period_days: int = 30
    messages_analyzed: int = 0
    confidence: float = Field(ge=0, le=1, default=0.0)

    ocean: OceanTraits = Field(default_factory=OceanTraits)
    dark_triad: DarkTriad = Field(default_factory=DarkTriad)
    communication: CommunicationProfile = Field(default_factory=CommunicationProfile)
    emotional_profile: EmotionalProfile = Field(default_factory=EmotionalProfile)
    topics: TopicsProfile = Field(default_factory=TopicsProfile)

    role_in_community: str = ""
    summary: str = ""

    ensemble_stats: EnsembleStats | None = None
