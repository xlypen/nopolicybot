#!/usr/bin/env python3
"""
Диагностика записи сообщений в БД.
Проверяет: storage_mode, последние сообщения в БД, тестовая запись.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import text
from db.engine import engine, init_db
from services.storage_cutover import get_storage_mode, storage_db_writes_enabled
from services.db_ingest import ingest_message_event


async def main() -> None:
    print("=== Диагностика DB ingest ===\n")

    mode = get_storage_mode()
    writes_ok = storage_db_writes_enabled(mode)
    print(f"1. Storage mode: {mode}")
    print(f"   DB writes enabled: {writes_ok}")
    if not writes_ok:
        print("   ⚠️  Запись в БД отключена! Проверьте data/storage_mode.json или STORAGE_MODE")
        return

    await init_db()
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT COUNT(*) FROM messages"))
        cnt = r.scalar()
        r2 = await conn.execute(
            text("SELECT id, chat_id, user_id, substr(text, 1, 40), sent_at FROM messages ORDER BY id DESC LIMIT 5")
        )
        rows = r2.fetchall()
    print(f"\n2. БД: всего сообщений = {cnt}")
    print("   Последние 5:")
    for row in rows:
        print(f"      id={row[0]} chat={row[1]} user={row[2]} text={row[3]!r}... at={row[4]}")

    import time
    test_msg_id = int(time.time() * 1000) % (2**31 - 1)
    print(f"\n3. Тестовая запись (chat_id=-999, user_id=999, message_id={test_msg_id})...")
    ok = await ingest_message_event(
        chat_id=-999,
        user_id=999,
        message_id=test_msg_id,
        text="[check_db_ingest test]",
        username="test",
        first_name="Test",
        media_type="text",
    )
    print(f"   ingest_message_event вернул: {ok}")

    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT id, chat_id, text FROM messages WHERE chat_id = -999"))
        test_rows = r.fetchall()
    if test_rows:
        print(f"   ✓ Тестовая запись найдена в БД: {test_rows[0]}")
        # Cleanup
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM messages WHERE chat_id = -999"))
        print("   (тестовая запись удалена)")
    else:
        print("   ✗ Тестовая запись НЕ появилась в БД!")

    print("\n=== Готово ===")


if __name__ == "__main__":
    asyncio.run(main())
