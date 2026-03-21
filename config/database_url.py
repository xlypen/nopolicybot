"""Сборка DATABASE_URL из POSTGRES_* / стандартных PG* если явная строка не задана."""

from __future__ import annotations

import os
from urllib.parse import quote_plus


def _env_first_nonempty(*keys: str) -> str:
    """Первое непустое значение из os.environ (POSTGRES_* и дубли libpq PG*)."""
    for key in keys:
        raw = os.getenv(key)
        if raw is None:
            continue
        val = str(raw).strip()
        if val:
            return val
    return ""


def build_postgresql_url_from_env(*, driver: str | None = None) -> str | None:
    """Собрать DSN из POSTGRES_* или PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (без чтения DATABASE_URL)."""
    port = _env_first_nonempty("POSTGRES_PORT", "PGPORT") or "5432"
    db = _env_first_nonempty("POSTGRES_DB", "PGDATABASE")
    user = _env_first_nonempty("POSTGRES_USER", "PGUSER")
    password = _env_first_nonempty("POSTGRES_PASSWORD", "PGPASSWORD")
    host = _env_first_nonempty("POSTGRES_HOST", "PGHOST")
    if not host:
        # Частый случай: в .env заданы только пользователь/БД/пароль, хост не указан — локальный Postgres.
        if db and user:
            host = "127.0.0.1"
        else:
            return None
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
