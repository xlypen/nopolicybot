"""Legacy JSON helpers kept for compatibility during migration."""

from __future__ import annotations

import json
from pathlib import Path

DB_PATH = Path("data/legacy_db.json")


def _init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        DB_PATH.write_text("{}", encoding="utf-8")


def load_db() -> dict:
    _init_db()
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_db(payload: dict) -> None:
    _init_db()
    DB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
