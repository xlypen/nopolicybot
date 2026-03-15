"""Tests for personality drift (P-4)."""

from services.personality.drift import calculate_drift_sync
from services.personality.schema import OceanTraits, PersonalityDrift, PersonalityProfile


def test_calculate_drift_sync_no_alert():
    """Small deltas — no alert."""
    p1 = PersonalityProfile(ocean=OceanTraits(openness=0.5, conscientiousness=0.5, extraversion=0.5, agreeableness=0.5, neuroticism=0.5))
    p2 = PersonalityProfile(ocean=OceanTraits(openness=0.52, conscientiousness=0.48, extraversion=0.51, agreeableness=0.49, neuroticism=0.50))
    drift = calculate_drift_sync([p1, p2], user_id="42", chat_id="1")
    assert drift is not None
    assert drift.alert is False
    assert abs(drift.deltas["openness"]) == 0.02
    assert drift.drift_score < 0.25


def test_calculate_drift_sync_alert_neuroticism():
    """Delta neuroticism > 0.20 — alert. [current, previous] so current has higher neuroticism."""
    previous = PersonalityProfile(ocean=OceanTraits(openness=0.5, conscientiousness=0.5, extraversion=0.5, agreeableness=0.5, neuroticism=0.4))
    current = PersonalityProfile(ocean=OceanTraits(openness=0.5, conscientiousness=0.5, extraversion=0.5, agreeableness=0.5, neuroticism=0.65))
    drift = calculate_drift_sync([current, previous], user_id="42", chat_id="1")
    assert drift is not None
    assert drift.alert is True
    assert "neuroticism" in drift.significant_changes
    assert drift.deltas["neuroticism"] == 0.25
    assert "нейротизм" in drift.alert_reason


def test_calculate_drift_sync_insufficient_profiles():
    assert calculate_drift_sync([], user_id="42") is None
    p = PersonalityProfile()
    assert calculate_drift_sync([p], user_id="42") is None
