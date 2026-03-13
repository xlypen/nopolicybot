from pathlib import Path

from services import audit_log


def test_audit_log_write_and_read_recent(tmp_path, monkeypatch):
    audit_file = Path(tmp_path) / "audit_events.jsonl"
    monkeypatch.setattr(audit_log, "_AUDIT_PATH", audit_file)

    first = audit_log.write_event("rate_limited", severity="warning", source="api_v2", payload={"ip": "127.0.0.1"})
    second = audit_log.write_event("api_exception", severity="error", source="api_v2", payload={"path": "/api/v2/graph/1"})

    assert first["event_type"] == "rate_limited"
    assert second["severity"] == "error"

    rows = audit_log.read_recent(limit=10)
    assert len(rows) == 2
    assert rows[-1]["event_type"] == "api_exception"
