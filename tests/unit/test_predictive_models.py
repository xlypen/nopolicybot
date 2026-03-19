from __future__ import annotations

from services import predictive_models as pm


def test_predict_toxicity_detects_upward_trend(monkeypatch):
    monkeypatch.setattr(
        pm,
        "_metrics_data",
        lambda: {
            "chats": {
                "1": {
                    "chat_daily": {
                        "2026-03-01": {"messages": 10, "toxic_messages": 1, "active_users": ["1", "2"]},
                        "2026-03-02": {"messages": 10, "toxic_messages": 2, "active_users": ["1", "2"]},
                        "2026-03-03": {"messages": 10, "toxic_messages": 3, "active_users": ["1", "2"]},
                        "2026-03-04": {"messages": 10, "toxic_messages": 4, "active_users": ["1", "2"]},
                    }
                }
            }
        },
    )
    out = pm.predict_toxicity(1, horizon_days=7, lookback_days=30)
    assert out["signal"] == "toxicity"
    assert out["predicted"] >= out["current"]
    assert out["direction"] in {"up", "flat"}


def test_predict_churn_uses_snapshot_series(monkeypatch):
    monkeypatch.setattr(
        pm,
        "_load_churn_snapshots",
        lambda: [
            {"chat_id": 1, "summary": {"users_considered": 10, "at_risk_count": 2}},
            {"chat_id": 1, "summary": {"users_considered": 10, "at_risk_count": 3}},
            {"chat_id": 1, "summary": {"users_considered": 10, "at_risk_count": 4}},
        ],
    )
    out = pm.predict_churn(1, horizon_days=7, lookback_days=30)
    assert out["signal"] == "churn_risk"
    assert "predicted" in out
    assert out["samples"] >= 3


def test_predict_overview_contract_all(monkeypatch):
    monkeypatch.setattr(
        pm,
        "_metrics_data",
        lambda: {"chats": {"1": {"chat_daily": {"2026-03-01": {"messages": 1, "toxic_messages": 0, "active_users": ["1"]}}}}},
    )
    monkeypatch.setattr(pm, "_load_churn_snapshots", lambda: [])
    from services import recommendations

    monkeypatch.setattr(
        recommendations,
        "build_retention_dashboard",
        lambda chat_id=None, days=30, limit=200: {"summary": {"users_considered": 1, "at_risk_count": 0}},
    )
    out = pm.predict_overview(None, horizon_days=7, lookback_days=30)
    assert out["chat_id"] == "all"
    assert set((out.get("signals") or {}).keys()) >= {"churn_risk", "toxicity", "virality"}
