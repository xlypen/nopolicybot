#!/usr/bin/env python3
"""
Миграция только PostgreSQL: messages.mention_user_ids (JSONB) + marketing_signal_events.

Как берётся URL:
  1) DATABASE_URL, если это postgresql* / postgres://
  2) иначе POSTGRES_* или стандартные PGHOST, PGDATABASE, PGUSER, PGPASSWORD (+ PGPORT)
     — даже если в .env для бота оставлен sqlite (частая ситуация).

Зависимости: sqlalchemy, psycopg2-binary

  .venv/bin/python scripts/apply_marketing_metrics_migration.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    # override=True: значения из .env перекрывают пустые/устаревшие переменные окружения
    # (иначе DATABASE_URL=sqlite из профиля и т.п. мешают собрать URL из POSTGRES_* / PG*).
    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass

try:
    from config.database_url import materialize_database_url_env, postgres_url_for_cli_scripts

    materialize_database_url_env()
except ImportError:
    postgres_url_for_cli_scripts = None  # type: ignore[misc, assignment]

    def materialize_database_url_env() -> None:  # type: ignore[misc]
        pass


def _is_postgres(url: str) -> bool:
    u = url.lower().strip()
    return u.startswith("postgresql") or u.startswith("postgres://")


def _sync_pg_url(url: str) -> str:
    u = url.strip()
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]
    sync_url = u.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "+psycopg2", 1)
    if sync_url.startswith("postgresql://") and "psycopg" not in sync_url.split("://", 1)[0]:
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return sync_url


def _resolve_database_url() -> str:
    if postgres_url_for_cli_scripts:
        url = postgres_url_for_cli_scripts()
        if url and _is_postgres(url):
            return url
    u = (os.getenv("DATABASE_URL") or "").strip()
    return u


def main() -> int:
    url = _resolve_database_url()
    if not url or not _is_postgres(url):
        print("Нет URL PostgreSQL.")
        print("")
        print("Задайте либо полный DATABASE_URL=postgresql+asyncpg://..., либо POSTGRES_HOST,")
        print("POSTGRES_DB, POSTGRES_USER (+ POSTGRES_PASSWORD) или те же роли через PGHOST,")
        print("PGDATABASE, PGUSER, PGPASSWORD (sqlite в DATABASE_URL будет заменён).")
        if url and not _is_postgres(url):
            print("")
            print(f"Сейчас DATABASE_URL начинается с: {url[:60]}…")
        return 1

    try:
        from sqlalchemy import create_engine, inspect, text
    except ImportError:
        print("Установите: .venv/bin/pip install sqlalchemy psycopg2-binary")
        return 1

    sync_url = _sync_pg_url(url)
    safe = sync_url.split("@")[-1] if "@" in sync_url else sync_url
    print(f"PostgreSQL: …@{safe}")

    try:
        engine = create_engine(sync_url, pool_pre_ping=True)
    except Exception as e:
        print(f"Ошибка подключения: {e}")
        return 1

    try:
        insp = inspect(engine)
        if insp.has_table("messages"):
            cols = {c["name"] for c in insp.get_columns("messages")}
            if "mention_user_ids" not in cols:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS "
                            "mention_user_ids JSONB NOT NULL DEFAULT '[]'::jsonb"
                        )
                    )
                print("Добавлена колонка messages.mention_user_ids.")
            else:
                print("Колонка messages.mention_user_ids уже есть.")
        else:
            print("Таблица messages нет — сначала создайте схему (init_db / alembic).")

        ddl = """
        CREATE TABLE IF NOT EXISTS marketing_signal_events (
            id BIGSERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            occurred_at TIMESTAMP NOT NULL,
            sentiment VARCHAR(16) NOT NULL DEFAULT 'neutral',
            is_political BOOLEAN NOT NULL DEFAULT FALSE
        );
        CREATE INDEX IF NOT EXISTS idx_mse_chat_time
            ON marketing_signal_events (chat_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_mse_user_chat_time
            ON marketing_signal_events (user_id, chat_id, occurred_at);
        """
        with engine.begin() as conn:
            for stmt in ddl.split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        print("Таблица marketing_signal_events готова.")
    except Exception as e:
        print(f"Ошибка миграции: {e}")
        return 1

    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
