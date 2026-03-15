"""Personality analysis service — structured profiles (OCEAN, Dark Triad, communication)."""

from services.personality.builder import build_structured_profile_from_messages
from services.personality.schema import (
    PersonalityProfile,
    OceanTraits,
    DarkTriad,
    DarkTriadTrait,
    CommunicationProfile,
    EmotionalProfile,
    TopicsProfile,
)

__all__ = [
    "build_structured_profile_from_messages",
    "PersonalityProfile",
    "OceanTraits",
    "DarkTriad",
    "DarkTriadTrait",
    "CommunicationProfile",
    "EmotionalProfile",
    "TopicsProfile",
]
