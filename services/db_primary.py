"""Определение основной БД приложения (PostgreSQL vs локальный SQLite)."""

from __future__ import annotations

import os


def db_primary_is_postgres() -> bool:
    """True, если основной DSN — PostgreSQL (не sqlite+aiosqlite и не файл bot.db)."""
    u = (os.getenv("DATABASE_URL") or "").strip().lower()
    if not u:
        try:
            from db.engine import DATABASE_URL as _du

            u = (_du or "").strip().lower()
        except Exception:
            u = ""
    return "postgresql" in u or u.startswith("postgres://")
