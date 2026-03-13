from __future__ import annotations

from services import decision_engine as de
from services.decision_engine import DecisionEngine, DecisionResult


def test_decision_engine_motivating_when_churn_high(monkeypatch):
    monkeypatch.setattr("services.decision_engine.learning_loop.compose_bias", lambda chat_id, user_id: {"variant": "control", "variant_enabled": True, "bias": {}})
    monkeypatch.setattr(
        "services.decision_engine.get_user_metrics",
        lambda user_id, chat_id=None, days=30: {
            "engagement_score": 0.2,
            "influence_score": 0.3,
            "churn_risk": 0.85,
        },
    )
    monkeypatch.setattr(
        "services.decision_engine.get_chat_health",
        lambda chat_id, days=30: {"health_score": 0.7},
    )
    engine = DecisionEngine()
    result = engine.decide(
        chat_id=1,
        user_id=100,
        sentiment="neutral",
        is_political=True,
        style="active",
        political_count=3,
    )
    assert result.strategy == "motivating"
    assert result.level_delta <= 0
    assert "high_churn_risk" in result.reasons


def test_decision_engine_careful_for_influential_user_in_bad_chat(monkeypatch):
    monkeypatch.setattr("services.decision_engine.learning_loop.compose_bias", lambda chat_id, user_id: {"variant": "control", "variant_enabled": True, "bias": {}})
    monkeypatch.setattr(
        "services.decision_engine.get_user_metrics",
        lambda user_id, chat_id=None, days=30: {
            "engagement_score": 0.6,
            "influence_score": 0.92,
            "churn_risk": 0.2,
        },
    )
    monkeypatch.setattr(
        "services.decision_engine.get_chat_health",
        lambda chat_id, days=30: {"health_score": 0.4},
    )
    engine = DecisionEngine()
    result = engine.decide(
        chat_id=1,
        user_id=100,
        sentiment="negative",
        is_political=True,
        style="active",
        political_count=5,
    )
    assert result.strategy == "careful"
    assert result.action_hint == "gentle_warning"


def test_decision_engine_strict_in_beast_mode_for_low_risk(monkeypatch):
    monkeypatch.setattr("services.decision_engine.learning_loop.compose_bias", lambda chat_id, user_id: {"variant": "control", "variant_enabled": True, "bias": {}})
    monkeypatch.setattr(
        "services.decision_engine.get_user_metrics",
        lambda user_id, chat_id=None, days=30: {
            "engagement_score": 0.9,
            "influence_score": 0.2,
            "churn_risk": 0.1,
        },
    )
    monkeypatch.setattr(
        "services.decision_engine.get_chat_health",
        lambda chat_id, days=30: {"health_score": 0.9},
    )
    engine = DecisionEngine()
    result = engine.decide(
        chat_id=1,
        user_id=100,
        sentiment="negative",
        is_political=True,
        style="beast",
        political_count=8,
    )
    assert result.strategy == "strict"
    assert result.level_delta >= 1


def test_decision_feedback_updates_quality_and_learning(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "_DECISIONS_PATH", tmp_path / "decision_events.json")
    monkeypatch.setattr("services.learning_loop._PATH", tmp_path / "learning_feedback.json")
    result = DecisionResult(
        strategy="standard",
        action_hint="standard_warning",
        level_delta=0,
        reasons=["default_policy"],
        metrics={"ab_variant": "control"},
    )
    event_id = de.append_decision_event(
        chat_id=1,
        user_id=42,
        sentiment="neutral",
        is_political=True,
        style="active",
        political_count=3,
        result=result,
        outcome="warning_sent",
        detail="unit-test",
    )
    updated = de.apply_decision_feedback(
        event_id=event_id,
        feedback="approve",
        reviewer="qa",
        note="looks good",
    )
    assert updated is not None
    assert updated["feedback_label"] == "approve"
    quality = de.get_decision_quality(chat_id=1, days=30)
    assert quality["feedback_count"] == 1
    assert quality["approval_rate"] == 1.0
