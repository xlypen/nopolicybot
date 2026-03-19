from __future__ import annotations

from services import storage_cutover as sc


def test_build_cutover_report_ready(monkeypatch):
    monkeypatch.setattr(
        sc,
        "export_snapshot",
        lambda: {
            "ok": True,
            "marker_present": False,
            "json": {"users": 10, "messages": 20, "edges": 5},
            "db": {"users": 10, "messages": 25, "edges": 6},
        },
    )
    monkeypatch.setattr(sc, "get_storage_mode", lambda: "hybrid")
    report = sc.build_cutover_report()
    assert report["ok"] is True
    assert report["db_ready_for_cutover"] is True
    assert report["recommended_mode"] == "db_only"


def test_apply_cutover_blocked_when_db_not_ready(monkeypatch):
    monkeypatch.setattr(
        sc,
        "build_cutover_report",
        lambda: {
            "ok": True,
            "db_ready_for_cutover": False,
            "storage_snapshot": {"json": {"users": 2, "messages": 3, "edges": 1}, "db": {"users": 1, "messages": 2, "edges": 1}},
        },
    )
    result = sc.apply_cutover("db", force=False, reason="test")
    assert result["ok"] is False
    assert result["error"] == "db_not_ready"


def test_get_storage_mode_accepts_new_env(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "_MODE_PATH", tmp_path / "storage_mode.json")
    monkeypatch.setenv("STORAGE_MODE", "db_first")
    assert sc.get_storage_mode() == "db_first"


def test_run_parity_check_once_writes_log(monkeypatch, tmp_path):
    log_path = tmp_path / "parity_diff.log"
    monkeypatch.setattr(sc, "_PARITY_DIFF_LOG", log_path)
    monkeypatch.setattr(
        sc,
        "export_snapshot",
        lambda: {
            "ok": True,
            "json": {"users": 10, "messages": 20, "edges": 5, "chats": 1},
            "db": {"users": 9, "messages": 22, "edges": 5, "chats": 1},
            "db_error": None,
        },
    )
    payload = sc.run_parity_check_once()
    assert payload["critical"] is True
    assert "users" in payload["critical_keys"]
    assert log_path.exists() is True
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
