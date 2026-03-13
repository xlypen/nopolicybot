from services import monitoring


def test_monitoring_snapshot_collects_metrics():
    app_name = "test_monitoring_app"
    for _ in range(5):
        monitoring.record_request(app_name, "GET", "/api/v2/graph/123", 200, 12.5)
    monitoring.record_request(app_name, "POST", "/api/v2/graph/123", 500, 120.0)

    snap = monitoring.snapshot(app_name)
    assert snap["requests_total"] >= 6
    assert int(snap["errors"]["5xx_total"]) >= 1
    assert float(snap["latency_ms"]["avg"]) > 0.0
    assert any(path == "/api/v2/graph/:id" for path, _count in (snap.get("top_paths") or []))


def test_monitoring_build_alerts_detects_high_error_rate():
    snap = {
        "requests_total": 100,
        "errors": {"5xx_rate": 0.08, "5xx_total": 8, "4xx_total": 2},
        "latency_ms": {"p95": 1800.0, "samples": 100},
    }
    alerts = monitoring.build_alerts(snap, recent_audit_events=[{"severity": "warning"}] * 6)
    kinds = {a.get("type") for a in alerts}
    assert "high_5xx_rate" in kinds
    assert "high_latency_p95" in kinds
    assert "security_audit_spike" in kinds


def test_monitoring_prometheus_export_contains_core_metrics():
    snap = {
        "requests_total": 12,
        "uptime_sec": 40,
        "latency_ms": {"p95": 22.3},
        "errors": {"5xx_total": 1},
    }
    text = monitoring.to_prometheus_text(snap, prefix="bot_test")
    assert "bot_test_requests_total 12" in text
    assert "bot_test_errors_5xx_total 1" in text
