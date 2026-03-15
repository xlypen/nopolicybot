"""Tests for personality verification (P-9)."""

from services.personality.schema import CommunicationProfile, OceanTraits, PersonalityProfile
from services.personality.verification import (
    BehavioralSignals,
    VerificationResult,
    compute_behavioral_signals,
    verify_profile,
)


def test_compute_behavioral_signals_empty():
    sig = compute_behavioral_signals([])
    assert sig.message_count == 0
    assert sig.conflict_ratio == 0.0


def test_compute_behavioral_signals_conflict():
    msgs = [
        {"text": "спор и конфликт"},
        {"text": "нормальное сообщение"},
        {"text": "ещё один срач"},
    ]
    sig = compute_behavioral_signals(msgs)
    assert sig.message_count == 3
    assert sig.conflict_ratio == 2 / 3
    assert sig.avg_message_length > 0


def test_verify_profile_high_correlation():
    profile = PersonalityProfile(
        communication=CommunicationProfile(conflict_tendency=0.7),
        ocean=OceanTraits(agreeableness=0.3, extraversion=0.6),
        confidence=0.8,
    )
    behavior = BehavioralSignals(
        message_count=20,
        conflict_ratio=0.65,
        avg_message_length=120,
    )
    result = verify_profile(profile, behavior)
    assert result.correlation_score >= 0
    assert result.reliability_badge in ("high", "medium", "low")
    assert isinstance(result.matched_dimensions, list)


def test_verify_profile_low_data():
    profile = PersonalityProfile()
    behavior = BehavioralSignals(message_count=3)
    result = verify_profile(profile, behavior)
    assert result.reliability_badge == "low"
    assert result.correlation_score == 0.5
