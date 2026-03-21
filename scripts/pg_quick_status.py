#!/usr/bin/env python3
"""
Краткая сводка по PostgreSQL (строки в основных таблицах).
Тот же выбор URL, что и у apply_marketing_metrics_migration.py.

  .venv/bin/python scripts/pg_quick_status.py
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

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from config.database_url import materialize_database_url_env, postgres_url_for_cli_scripts  # noqa: E402

materialize_database_url_env()


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


def main() -> int:
    url = postgres_url_for_cli_scripts() or ""
    if not url or not _is_postgres(url):
        print("Нет PostgreSQL URL. См. вывод apply_marketing_metrics_migration.py без аргументов.")
        return 1

    try:
        from sqlalchemy import create_engine, inspect, text
    except ImportError:
        print("pip install sqlalchemy psycopg2-binary")
        return 1

    sync_url = _sync_pg_url(url)
    tail = sync_url.split("@")[-1] if "@" in sync_url else sync_url
    print(f"Подключение: …@{tail}\n")

    try:
        engine = create_engine(sync_url, pool_pre_ping=True)
    except Exception as e:
        print(f"Ошибка: {e}")
        return 1

    tables = [
        "users",
        "messages",
        "edges",
        "marketing_signal_events",
        "graph_snapshots",
        "chat_settings",
    ]

    with engine.connect() as conn:
        insp = inspect(engine)
        for t in tables:
            if not insp.has_table(t):
                print(f"{t}: (нет таблицы)")
                continue
            n = conn.execute(text(f'SELECT COUNT(*) AS c FROM "{t}"')).scalar()
            print(f"{t}: {int(n or 0)} строк")

        if insp.has_table("messages"):
            cols = {c["name"] for c in insp.get_columns("messages")}
            print(f"\nmessages.mention_user_ids: {'да' if 'mention_user_ids' in cols else 'НЕТ (нужна миграция)'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
