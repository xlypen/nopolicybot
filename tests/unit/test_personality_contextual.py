"""Tests for contextual profiles by topic (P-5)."""

from services.personality.contextual import (
    build_context_profiles,
    enrich_profile_with_context,
    _group_messages_by_topic,
    _topic_for_message,
)
from services.personality.schema import ContextProfile, PersonalityProfile


def test_topic_for_message():
    assert _topic_for_message("путин и политика") == "politics"
    assert _topic_for_message("api сервер код") == "technical"
    assert _topic_for_message("мем шутка лол") == "humor"
    assert _topic_for_message("спор и конфликт") == "conflict"
    assert _topic_for_message("привет как дела") == "general"


def test_group_messages_by_topic():
    msgs = [
        {"text": "путин", "date": "2025-01-01"},
        {"text": "api код", "date": "2025-01-02"},
        {"text": "путин снова", "date": "2025-01-03"},
    ]
    by = _group_messages_by_topic(msgs)
    assert "politics" in by
    assert len(by["politics"]) == 2
    assert "technical" in by
    assert len(by["technical"]) == 1


def test_build_context_profiles_skips_small_topics():
    msgs = [{"text": "путин", "date": "2025-01-01"} for _ in range(10)]
    ctx = build_context_profiles(msgs)
    assert ctx == {}


def test_build_context_profiles_builds_for_large_topic():
    msgs = [{"text": "путин политика выборы", "date": "2025-01-01"} for _ in range(20)]
    ctx = build_context_profiles(msgs)
    assert "politics" in ctx
    p = ctx["politics"]
    assert isinstance(p, ContextProfile)
    assert p.messages_count == 20
    assert "openness" in p.ocean
    assert 0 <= p.conflict_tendency <= 1


def test_build_context_profiles_conflict_topic():
    msgs = [{"text": "спор срач конфликт агрессия", "date": "2025-01-01"} for _ in range(20)]
    ctx = build_context_profiles(msgs)
    assert "conflict" in ctx
    p = ctx["conflict"]
    assert p.conflict_tendency > 0.5


def test_enrich_profile_with_context():
    profile = PersonalityProfile(user_id="1", username="u")
    msgs = [{"text": "путин политика", "date": "2025-01-01"} for _ in range(20)]
    enriched = enrich_profile_with_context(profile, msgs)
    assert enriched.context_profiles
    assert "politics" in enriched.context_profiles
