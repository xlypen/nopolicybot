from __future__ import annotations

from services import recommendations


def test_pick_at_risk_for_outreach_prioritizes_influence(monkeypatch):
    monkeypatch.setattr(
        recommendations,
        "build_retention_dashboard",
        lambda chat_id=None, days=30, limit=500: {
            "at_risk": [
                {"user_id": 1, "churn_risk": 0.8, "influence_score": 0.2, "engagement_score": 0.4},
                {"user_id": 2, "churn_risk": 0.75, "influence_score": 0.9, "engagement_score": 0.4},
                {"user_id": 3, "churn_risk": 0.7, "influence_score": 0.8, "engagement_score": 0.4},
            ]
        },
    )
    rows = recommendations.pick_at_risk_for_outreach(1, min_churn_risk=0.72, limit=2)
    assert [x["user_id"] for x in rows] == [2, 1]


def test_retention_dm_cooldown_state(tmp_path, monkeypatch):
    monkeypatch.setattr(recommendations, "_OUTREACH_STATE_PATH", tmp_path / "retention_actions.json")

    assert recommendations.should_send_retention_dm(100, 200, cooldown_hours=24) is True
    recommendations.mark_retention_dm_sent(100, 200)
    assert recommendations.should_send_retention_dm(100, 200, cooldown_hours=24) is False
