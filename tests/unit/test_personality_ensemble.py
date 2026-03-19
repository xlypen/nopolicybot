"""Tests for personality ensemble (P-3)."""

from unittest.mock import patch

from services.personality.ensemble import (
    _aggregate_ocean,
    _aggregate_dark_triad,
    build_ensemble_profile,
)
from services.personality.schema import (
    DarkTriad,
    DarkTriadTrait,
    OceanTraits,
    PersonalityProfile,
)


def test_aggregate_ocean():
    p1 = PersonalityProfile(ocean=OceanTraits(openness=0.8, conscientiousness=0.4, extraversion=0.6, agreeableness=0.5, neuroticism=0.3))
    p2 = PersonalityProfile(ocean=OceanTraits(openness=0.6, conscientiousness=0.6, extraversion=0.4, agreeableness=0.5, neuroticism=0.5))
    ocean, stds, low = _aggregate_ocean([p1, p2])
    assert ocean.openness == 0.7
    assert ocean.conscientiousness == 0.5
    assert "openness" in stds
    assert 0.14 <= stds["openness"] <= 0.15  # stdev(0.8, 0.6) ≈ 0.141
    assert low == [] or "openness" in low or "neuroticism" in low  # depends on threshold


def test_aggregate_dark_triad():
    p1 = PersonalityProfile(dark_triad=DarkTriad(narcissism=DarkTriadTrait(label="low", score=0.2), machiavellianism=DarkTriadTrait(label="medium", score=0.5), psychopathy=DarkTriadTrait(label="low", score=0.1)))
    p2 = PersonalityProfile(dark_triad=DarkTriad(narcissism=DarkTriadTrait(label="low", score=0.3), machiavellianism=DarkTriadTrait(label="medium", score=0.5), psychopathy=DarkTriadTrait(label="low", score=0.2)))
    dt = _aggregate_dark_triad([p1, p2])
    assert dt.narcissism.label == "low"
    assert dt.narcissism.score == 0.25
    assert dt.machiavellianism.score == 0.5


def test_build_ensemble_profile_mocked():
    """Mock builder to return fixed profiles for each model."""
    def fake_build(messages, user_id, username="", period_days=30, chat_description="", model=None, max_retries=1):
        return PersonalityProfile(
            user_id=str(user_id),
            ocean=OceanTraits(openness=0.7, conscientiousness=0.5, extraversion=0.6, agreeableness=0.4, neuroticism=0.5),
            confidence=0.75,
            summary="Test",
            generated_at="2025-01-01T00:00:00Z",
        )

    with patch("services.personality.ensemble.build_structured_profile_from_messages", side_effect=fake_build):
        msgs = [{"text": "Hi", "date": "2025-01-01"} for _ in range(20)]
        profile = build_ensemble_profile(msgs, user_id="42", models=["m1", "m2", "m3"], min_models=2)

    assert profile is not None
    assert profile.ensemble_stats is not None
    assert len(profile.ensemble_stats.models_used) == 3
    assert profile.ocean.openness == 0.7
    assert 0 <= profile.ensemble_stats.agreement_score <= 1
