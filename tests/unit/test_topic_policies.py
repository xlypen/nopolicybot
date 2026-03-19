from __future__ import annotations

from services import topic_policies as tp


def test_default_policies_present(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_POLICIES_PATH", tmp_path / "topic_policies.json")
    policies = tp.get_topic_policies()
    assert "politics" in policies
    assert policies["politics"]["action"] == "moderate"


def test_set_and_detect_custom_topic(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_POLICIES_PATH", tmp_path / "topic_policies.json")
    tp.set_topic_policy(
        "crypto",
        {
            "enabled": True,
            "action": "moderate",
            "priority": 80,
            "label": "Крипта",
            "keywords": ["биткоин", "eth", "airdrop"],
        },
    )
    result = tp.resolve_topic_trigger("Сегодня биткоин снова растет")
    assert "crypto" in result["matched_topics"]
    assert result["trigger_topic"] == "crypto"


def test_special_matcher_for_politics(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_POLICIES_PATH", tmp_path / "topic_policies.json")
    result = tp.resolve_topic_trigger(
        "обсуждаем президента",
        special_matchers={"politics": lambda text: "президент" in text.lower()},
    )
    assert result["trigger_topic"] == "politics"
