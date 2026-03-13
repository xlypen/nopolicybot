from services.moderation_risk import build_moderation_risk


def test_risk_payload_shape():
    payload = build_moderation_risk(None)
    assert set(payload.keys()) >= {"risk_messages_7d", "top_red_flags", "tone_context", "tone_risk_score_pct"}


def test_risk_score_range():
    payload = build_moderation_risk(None)
    assert 0 <= payload["tone_risk_score_pct"] <= 100
