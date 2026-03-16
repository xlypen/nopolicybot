"""Tests for personality profile builder (P-2)."""

import json
from unittest.mock import MagicMock, patch

from services.personality.builder import _extract_json, _format_messages, build_structured_profile_from_messages
from services.personality.schema import PersonalityProfile


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_markdown():
    raw = 'Here is the result:\n```json\n{"ocean": {"openness": 0.7}}\n```'
    assert "openness" in _extract_json(raw)
    data = json.loads(_extract_json(raw))
    assert data["ocean"]["openness"] == 0.7


def test_extract_json_no_backticks():
    raw = 'Some text before {"key": "value"} and after'
    assert _extract_json(raw) == '{"key": "value"}'


def test_format_messages():
    msgs = [
        {"text": "Hello", "date": "2025-01-01"},
        {"text": "World", "date": "2025-01-02"},
    ]
    out = _format_messages(msgs)
    assert "[2025-01-01]" in out
    assert "Hello" in out
    assert "[2025-01-02]" in out
    assert "World" in out


def test_format_messages_empty():
    assert _format_messages([]) == ""
    assert _format_messages([{"text": "", "date": "2025-01-01"}]) == ""


def test_build_structured_profile_mocked():
    """Mock LLM to return valid JSON."""
    fake_response = {
        "ocean": {"openness": 0.72, "conscientiousness": 0.45, "extraversion": 0.81, "agreeableness": 0.38, "neuroticism": 0.61},
        "dark_triad": {
            "narcissism": {"label": "low", "score": 0.21},
            "machiavellianism": {"label": "medium", "score": 0.48},
            "psychopathy": {"label": "low", "score": 0.15},
        },
        "communication": {"style": "assertive", "conflict_tendency": 0.65, "influence_seeking": 0.55, "emotional_expressiveness": 0.70, "topic_consistency": 0.40},
        "emotional_profile": {"valence": 0.42, "arousal": 0.68, "dominant_emotions": ["раздражение"]},
        "topics": {"primary": ["политика"], "secondary": [], "avoided": []},
        "role_in_community": "provocateur",
        "summary": "Тестовое резюме.",
        "confidence": 0.78,
    }

    fake_text = json.dumps(fake_response)

    with patch("services.personality.builder.chat_complete_with_fallback", return_value=(fake_text, "test-model")), \
         patch("services.personality.builder.prefer_free_mode", return_value=False):
        msgs = [{"text": "Test message", "date": "2025-01-01"} for _ in range(10)]
        profile = build_structured_profile_from_messages(msgs, user_id="42", username="test", period_days=30)

    assert profile is not None
    assert profile.user_id == "42"
    assert profile.ocean.openness == 0.72
    assert profile.confidence == 0.78
    assert profile.summary == "Тестовое резюме."


def test_build_returns_none_for_empty_messages():
    assert build_structured_profile_from_messages([], user_id="42") is None
