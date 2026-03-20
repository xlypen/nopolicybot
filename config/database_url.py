"""Сборка DATABASE_URL из POSTGRES_* если явная строка не задана."""

from __future__ import annotations

import os
from urllib.parse import quote_plus


def materialize_database_url_env() -> None:
    """Если DATABASE_URL пустой, собрать из POSTGRES_HOST, POSTGRES_USER, …"""
    if str(os.getenv("DATABASE_URL", "") or "").strip():
        return
    host = str(os.getenv("POSTGRES_HOST", "") or "").strip()
    if not host:
        return
    port = str(os.getenv("POSTGRES_PORT", "5432") or "5432").strip()
    db = str(os.getenv("POSTGRES_DB", "") or "").strip()
    user = str(os.getenv("POSTGRES_USER", "") or "").strip()
    password = str(os.getenv("POSTGRES_PASSWORD", "") or "").strip()
    if not (db and user):
        return
    driver = str(os.getenv("POSTGRES_DRIVER", "asyncpg") or "asyncpg").strip()
    pw = quote_plus(password) if password else ""
    auth = f"{quote_plus(user)}:{pw}@" if password else f"{quote_plus(user)}@"
    os.environ["DATABASE_URL"] = f"postgresql+{driver}://{auth}{host}:{port}/{db}"
