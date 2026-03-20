from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from services.data_platform import export_snapshot

_MODE_PATH = Path(__file__).resolve().parent.parent / "data" / "storage_mode.json"
_MIGRATION_MARKER = Path(__file__).resolve().parent.parent / ".sqlite_migrated_from_json"
_PARITY_DIFF_LOG = Path(__file__).resolve().parent.parent / "data" / "parity_diff.log"
_ALLOWED_MODES = {"json", "dual", "db_first", "db_only"}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=path.parent) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _normalize_mode(mode: str | None) -> str:
    raw = str(mode or "").strip().lower()
    aliases = {
        "hybrid": "dual",
        "db": "db_only",
        "database": "db_only",
    }
    normalized = aliases.get(raw, raw)
    if normalized in _ALLOWED_MODES:
        return normalized
    return "dual"


def storage_db_reads_enabled(mode: str | None = None) -> bool:
    return _normalize_mode(mode or get_storage_mode()) in {"dual", "db_first", "db_only"}


def storage_db_writes_enabled(mode: str | None = None) -> bool:
    return _normalize_mode(mode or get_storage_mode()) in {"dual", "db_first", "db_only"}


def storage_json_fallback_enabled(mode: str | None = None) -> bool:
    return _normalize_mode(mode or get_storage_mode()) in {"dual", "db_first"}


def storage_db_only_mode(mode: str | None = None) -> bool:
    return _normalize_mode(mode or get_storage_mode()) == "db_only"


def storage_json_writes_enabled(mode: str | None = None) -> bool:
    """When db_only, JSON writes are disabled (DB is source of truth)."""
    return not storage_db_only_mode(mode)


def get_storage_mode() -> str:
    """Режим хранилища: приоритет у переменных окружения, затем storage_mode.json."""
    env_mode = (os.getenv("STORAGE_MODE") or os.getenv("STORAGE_PRIMARY") or "").strip().lower()
    if env_mode:
        return _normalize_mode(env_mode)
    if _MODE_PATH.exists():
        try:
            payload = json.loads(_MODE_PATH.read_text(encoding="utf-8"))
            return _normalize_mode(str(payload.get("mode", "")).strip().lower())
        except Exception:
            pass
    # После миграции в SQLite по умолчанию только таблицы, без user_stats/social_graph JSON.
    if _MIGRATION_MARKER.exists():
        return _normalize_mode("db_only")
    return _normalize_mode("dual")


def set_storage_mode(mode: str, *, reason: str = "manual") -> dict:
    mode_norm = _normalize_mode(mode)
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
        "recommended_mode": "db_only" if ready else mode,
    }
    return report


def apply_cutover(mode: str, *, force: bool = False, reason: str = "manual") -> dict:
    mode_norm = _normalize_mode(mode)
    if mode_norm not in _ALLOWED_MODES:
        return {"ok": False, "error": f"unsupported mode: {mode}"}
    report = build_cutover_report()
    ready = bool(report.get("db_ready_for_cutover"))
    if mode_norm == "db_only" and not ready and not force:
        return {
            "ok": False,
            "error": "db_not_ready",
            "report": report,
        }
    saved = set_storage_mode(mode_norm, reason=reason)
    marker_written = False
    if mode_norm == "db_only" and (ready or force):
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


def _build_parity_payload(snapshot: dict) -> dict:
    json_counts = snapshot.get("json") or {}
    db_counts = snapshot.get("db") or {}
    deltas = {
        "users": int(db_counts.get("users", 0) or 0) - int(json_counts.get("users", 0) or 0),
        "messages": int(db_counts.get("messages", 0) or 0) - int(json_counts.get("messages", 0) or 0),
        "edges": int(db_counts.get("edges", 0) or 0) - int(json_counts.get("edges", 0) or 0),
    }
    critical = [k for k, v in deltas.items() if int(v) < 0]
    return {
        "ts": _now_iso(),
        "mode": get_storage_mode(),
        "json": {k: int(json_counts.get(k, 0) or 0) for k in ("users", "messages", "edges", "chats")},
        "db": {k: int(db_counts.get(k, 0) or 0) for k in ("users", "messages", "edges", "chats")},
        "delta_db_minus_json": deltas,
        "critical": bool(critical),
        "critical_keys": critical,
        "db_error": snapshot.get("db_error"),
    }


def run_parity_check_once() -> dict:
    snapshot = export_snapshot()
    payload = _build_parity_payload(snapshot if isinstance(snapshot, dict) else {})
    _PARITY_DIFF_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _PARITY_DIFF_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload
