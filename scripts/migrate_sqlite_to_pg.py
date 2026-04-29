#!/usr/bin/env python3
"""Однопроходная миграция SQLite (data/bot.db) → PostgreSQL (nopolicybot DB).

Стратегия: TRUNCATE целевых таблиц в PG → COPY данных из SQLite. Для
безопасности есть аргумент --check который только сверяет кол-во строк.

Все таблицы из db.models.Base.metadata. Большие BLOB/JSON колонки обрабатываются
через адаптеры SQLAlchemy (Base.metadata уже знает типы).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Гарантированно работаем из корня проекта.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault('POSTGRES_HOST', '127.0.0.1')
os.environ.setdefault('POSTGRES_PORT', '5432')
os.environ.setdefault('POSTGRES_DB', 'nopolicybot')
os.environ.setdefault('POSTGRES_USER', 'nopolicybot')
os.environ.setdefault('POSTGRES_PASSWORD', 'postgres')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://nopolicybot:postgres@127.0.0.1:5432/nopolicybot')

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from db.models import Base

SQLITE_URL = f"sqlite:///{ROOT}/data/bot.db"
PG_SYNC_URL = "postgresql+psycopg2://nopolicybot:postgres@127.0.0.1:5432/nopolicybot"

# Порядок миграции: сначала родители, потом зависимые.
MIGRATION_ORDER = [
    "users",
    "chat_settings",
    "storage_chats",
    "storage_settings",
    "messages",
    "user_message_archive",
    "user_profiles",
    "user_portraits",
    "dialogue_log",
    "dialogue_messages",
    "edges",
    "graph_snapshots",
    "processed_dates",
    "personality_profiles",
    "personality_portraits",
    # marketing_metrics_chats: в SQLite таблицы нет, в PG есть — НЕ трогаем
    "marketing_signal_events",
    "sparring_weekly_fighters",
]


def _row_count(eng, table: str) -> int:
    try:
        with eng.connect() as c:
            return int(c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    except Exception:
        return -1  # таблица не существует


def _copy_table(sqlite_eng, pg_eng, table_name: str) -> tuple[int, int]:
    """Возвращает (читано_из_sqlite, записано_в_pg)."""
    table = Base.metadata.tables.get(table_name)
    if table is None:
        print(f"  [SKIP] {table_name}: нет в Base.metadata")
        return (0, 0)

    src_n = _row_count(sqlite_eng, table_name)
    if src_n <= 0:
        print(f"  [empty] {table_name}: {src_n} в SQLite")
        return (0, 0)

    cols = [c.name for c in table.columns]
    col_list = ", ".join(cols)

    SqliteSession = sessionmaker(bind=sqlite_eng)
    PgSession = sessionmaker(bind=pg_eng)
    sql_sess = SqliteSession()
    pg_sess = PgSession()
    try:
        # TRUNCATE целевой таблицы (CASCADE на случай FK).
        with pg_eng.begin() as conn:
            conn.execute(text(f'TRUNCATE TABLE "{table_name}" RESTART IDENTITY CASCADE'))
        # Чтение из SQLite чанками.
        rows = sql_sess.execute(text(f"SELECT {col_list} FROM {table_name}")).fetchall()
        if not rows:
            return (0, 0)
        # Запись батчем через bulk insert mappings.
        dicts = [dict(zip(cols, r)) for r in rows]
        chunk = 500
        written = 0
        for i in range(0, len(dicts), chunk):
            batch = dicts[i:i + chunk]
            pg_sess.execute(table.insert(), batch)
            written += len(batch)
        pg_sess.commit()
        return (len(rows), written)
    finally:
        sql_sess.close()
        pg_sess.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Только сверить row counts, без записи")
    ap.add_argument("--dry-run", action="store_true", help="Чтение без записи (truncate тоже не делать)")
    args = ap.parse_args()

    sqlite_eng = create_engine(SQLITE_URL, echo=False)
    pg_eng = create_engine(PG_SYNC_URL, echo=False)

    if args.check:
        print(f"{'TABLE':<32} {'SQLITE':>10} {'PG':>10}")
        for t in MIGRATION_ORDER:
            s = _row_count(sqlite_eng, t)
            p = _row_count(pg_eng, t)
            mark = "" if s == p else " ⚠"
            print(f"{t:<32} {s:>10} {p:>10}{mark}")
        return 0

    print("=== Migration SQLite → PG ===")
    total_read = 0
    total_written = 0
    for t in MIGRATION_ORDER:
        if args.dry_run:
            n = _row_count(sqlite_eng, t)
            print(f"  [dry-run] {t}: SQLite has {n}")
            continue
        try:
            r, w = _copy_table(sqlite_eng, pg_eng, t)
            total_read += r
            total_written += w
            mark = "OK" if r == w else "MISMATCH"
            print(f"  [{mark}] {t}: read={r} write={w}")
        except Exception as e:
            print(f"  [ERR] {t}: {e}")
            return 1
    print(f"\nTotal: read={total_read} written={total_written}")
    print("\n=== Verify ===")
    print(f"{'TABLE':<32} {'SQLITE':>10} {'PG':>10}")
    for t in MIGRATION_ORDER:
        s = _row_count(sqlite_eng, t)
        p = _row_count(pg_eng, t)
        mark = "" if s == p else " ⚠"
        print(f"{t:<32} {s:>10} {p:>10}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
