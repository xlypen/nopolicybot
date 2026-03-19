from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

_LOCK = Lock()
_BASE_DIR = Path(__file__).resolve().parent.parent
_AUDIT_PATH = _BASE_DIR / "data" / "audit_events.jsonl"


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def write_event(event_type: str, severity: str = "info", source: str = "system", payload: dict | None = None) -> dict:
    evt = {
        "ts": _now(),
        "event_type": str(event_type or "event"),
        "severity": str(severity or "info"),
        "source": str(source or "system"),
        "payload": payload if isinstance(payload, dict) else {},
    }
    line = json.dumps(evt, ensure_ascii=False)
    with _LOCK:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return evt


def read_recent(limit: int = 100) -> list[dict]:
    cap = max(1, min(1000, int(limit or 100)))
    if not _AUDIT_PATH.exists():
        return []
    with _LOCK:
        lines = deque(maxlen=cap)
        with _AUDIT_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                lines.append(line.rstrip("\n"))
    out: list[dict] = []
    for raw in lines:
        if not raw:
            continue
        try:
            row = json.loads(raw)
            if isinstance(row, dict):
                out.append(row)
        except Exception:
            continue
    return out
