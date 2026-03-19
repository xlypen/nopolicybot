"""Personality analysis service — structured profiles (OCEAN, Dark Triad, communication)."""

from services.personality.builder import build_structured_profile_from_messages
from services.personality.drift import calculate_drift, calculate_drift_sync
from services.personality.ensemble import build_ensemble_profile
from services.personality.contextual import build_context_profiles, enrich_profile_with_context
from services.personality.verification import (
    BehavioralSignals,
    VerificationResult,
    compute_behavioral_signals,
    verify_profile,
)
from services.personality.schema import (
    ContextProfile,
    PersonalityDrift,
    PersonalityProfile,
    OceanTraits,
    DarkTriad,
    DarkTriadTrait,
    CommunicationProfile,
    EmotionalProfile,
    TopicsProfile,
)

__all__ = [
    "build_ensemble_profile",
    "build_context_profiles",
    "enrich_profile_with_context",
    "calculate_drift",
    "calculate_drift_sync",
    "build_structured_profile_from_messages",
    "PersonalityProfile",
    "ContextProfile",
    "OceanTraits",
    "DarkTriad",
    "DarkTriadTrait",
    "CommunicationProfile",
    "EmotionalProfile",
    "PersonalityDrift",
    "TopicsProfile",
    "BehavioralSignals",
    "VerificationResult",
    "compute_behavioral_signals",
    "verify_profile",
]
