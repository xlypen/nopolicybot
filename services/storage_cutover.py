from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from services.data_platform import export_snapshot

_MODE_PATH = Path(__file__).resolve().parent.parent / "data" / "storage_mode.json"
_MIGRATION_MARKER = Path(__file__).resolve().parent.parent / ".sqlite_migrated_from_json"
_ALLOWED_MODES = {"json", "hybrid", "db"}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=path.parent) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def get_storage_mode() -> str:
    if _MODE_PATH.exists():
        try:
            payload = json.loads(_MODE_PATH.read_text(encoding="utf-8"))
            mode = str(payload.get("mode", "")).strip().lower()
            if mode in _ALLOWED_MODES:
                return mode
        except Exception:
            pass
    env_mode = (os.getenv("STORAGE_PRIMARY") or "hybrid").strip().lower()
    if env_mode in _ALLOWED_MODES:
        return env_mode
    return "hybrid"


def set_storage_mode(mode: str, *, reason: str = "manual") -> dict:
    mode_norm = str(mode or "").strip().lower()
    if mode_norm not in _ALLOWED_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    payload = {
        "mode": mode_norm,
        "updated_at": _now_iso(),
        "reason": str(reason or "manual")[:200],
    }
    _save_json(_MODE_PATH, payload)
    return payload


def _db_not_behind(snapshot: dict) -> tuple[bool, list[str]]:
    json_counts = snapshot.get("json") or {}
    db_counts = snapshot.get("db") or {}
    checks = []
    for key in ("users", "messages", "edges"):
        checks.append(int(db_counts.get(key, 0) or 0) >= int(json_counts.get(key, 0) or 0))
    reasons = []
    if not checks[0]:
        reasons.append("db users count is behind json")
    if not checks[1]:
        reasons.append("db messages count is behind json")
    if not checks[2]:
        reasons.append("db edges count is behind json")
    return all(checks), reasons


def build_cutover_report() -> dict:
    snapshot = export_snapshot()
    ready, reasons = _db_not_behind(snapshot)
    mode = get_storage_mode()
    report = {
        "ok": True,
        "current_mode": mode,
        "marker_present": bool(snapshot.get("marker_present", False)),
        "storage_snapshot": snapshot,
        "db_ready_for_cutover": bool(ready),
        "blocking_reasons": reasons,
        "recommended_mode": "db" if ready else mode,
    }
    return report


def apply_cutover(mode: str, *, force: bool = False, reason: str = "manual") -> dict:
    mode_norm = str(mode or "").strip().lower()
    if mode_norm not in _ALLOWED_MODES:
        return {"ok": False, "error": f"unsupported mode: {mode}"}
    report = build_cutover_report()
    ready = bool(report.get("db_ready_for_cutover"))
    if mode_norm == "db" and not ready and not force:
        return {
            "ok": False,
            "error": "db_not_ready",
            "report": report,
        }
    saved = set_storage_mode(mode_norm, reason=reason)
    marker_written = False
    if mode_norm == "db" and (ready or force):
        marker = {
            "at": _now_iso(),
            "mode": mode_norm,
            "forced": bool(force),
            "reason": str(reason or "manual")[:200],
            "snapshot": report.get("storage_snapshot"),
        }
        _save_json(_MIGRATION_MARKER, marker)
        marker_written = True
    out = {
        "ok": True,
        "mode": mode_norm,
        "saved": saved,
        "marker_written": marker_written,
        "report": build_cutover_report(),
    }
    return out
