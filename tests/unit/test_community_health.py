from services.community_health import build_community_health


def test_health_payload_shape():
    payload = build_community_health(None)
    assert set(payload.keys()) >= {"dau", "wau", "mau", "retention_rows", "response_time", "tone_context", "forecast_7d"}


def test_health_ordering_non_negative():
    payload = build_community_health(None)
    assert payload["dau"] >= 0
    assert payload["wau"] >= 0
    assert payload["mau"] >= 0
