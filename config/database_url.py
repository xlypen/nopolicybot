"""Сборка DATABASE_URL из POSTGRES_* если явная строка не задана."""

from __future__ import annotations

import os
from urllib.parse import quote_plus


def build_postgresql_url_from_env(*, driver: str | None = None) -> str | None:
    """Собрать DSN PostgreSQL только из POSTGRES_* (без чтения DATABASE_URL)."""
    host = str(os.getenv("POSTGRES_HOST", "") or "").strip()
    if not host:
        return None
    port = str(os.getenv("POSTGRES_PORT", "5432") or "5432").strip()
    db = str(os.getenv("POSTGRES_DB", "") or "").strip()
    user = str(os.getenv("POSTGRES_USER", "") or "").strip()
    password = str(os.getenv("POSTGRES_PASSWORD", "") or "").strip()
    if not (db and user):
        return None
    drv = str(driver or os.getenv("POSTGRES_DRIVER", "asyncpg") or "asyncpg").strip()
    pw = quote_plus(password) if password else ""
    auth = f"{quote_plus(user)}:{pw}@" if password else f"{quote_plus(user)}@"
    return f"postgresql+{drv}://{auth}{host}:{port}/{db}"


def materialize_database_url_env() -> None:
    """Если заданы POSTGRES_*: подставить DATABASE_URL; sqlite в .env при этом заменяется.

    Уже заданный в .env ``postgresql+…`` / ``postgres://`` не перезаписываем (полная строка важнее).
    """
    pg = build_postgresql_url_from_env()
    if not pg:
        return
    cur = str(os.getenv("DATABASE_URL", "") or "").strip().lower()
    if cur.startswith("postgresql") or cur.startswith("postgres://"):
        return
    os.environ["DATABASE_URL"] = pg


def postgres_url_for_cli_scripts() -> str | None:
    """
    URL для CLI после той же логики, что и у приложения (materialize перекрывает sqlite).
    """
    materialize_database_url_env()
    raw = str(os.getenv("DATABASE_URL", "") or "").strip()
    low = raw.lower()
    if low.startswith("postgresql") or raw.startswith("postgres://"):
        return raw
    return build_postgresql_url_from_env()
