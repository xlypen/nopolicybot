#!/usr/bin/env python3
"""
migrate_to_sqlite.py — Разовая миграция данных из user_stats.json в SQLite.

Использование:
    cd /opt/telegram-political-monitor-bot
    venv/bin/python migrate_to_sqlite.py

Что делает:
    1. Читает user_stats.json
    2. Создаёт (или обновляет) user_stats.db через db.py
    3. Сохраняет все записи пользователей и чатов
    4. Выводит статистику

После миграции установите USE_SQLITE=true в файле окружения бота
(напр. /etc/systemd/system/telegram-political-monitor-bot.service или .env)
и перезапустите сервис: systemctl restart telegram-political-monitor-bot
"""

import json
import sys
from pathlib import Path

# Убедимся что мы в нужной директории
SCRIPT_DIR = Path(__file__).resolve().parent
USERS_JSON = SCRIPT_DIR / "user_stats.json"


def main() -> None:
    print("=== migrate_to_sqlite.py ===")
    print(f"Исходный файл: {USERS_JSON}")

    if not USERS_JSON.exists():
        print("ОШИБКА: user_stats.json не найден. Нечего мигрировать.")
        sys.exit(1)

    # Загружаем JSON
    try:
        raw = USERS_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as e:
        print(f"ОШИБКА чтения JSON: {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        print("ОШИБКА: user_stats.json имеет неверный формат (ожидается dict).")
        sys.exit(1)

    users = data.get("users") or {}
    chats = data.get("chats") or {}
    print(f"Найдено пользователей: {len(users)}")
    print(f"Найдено чатов: {len(chats)}")

    # Импортируем db.py
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from db import save_db, DB_PATH, _init_db
        _init_db()
        print(f"База данных: {DB_PATH}")
    except ImportError as e:
        print(f"ОШИБКА импорта db.py: {e}")
        sys.exit(1)

    # Мигрируем
    print("\nМиграция...")
    try:
        save_db(data)
        print(f"✓ Записано пользователей: {len(users)}")
        print(f"✓ Записано чатов: {len(chats)}")
    except Exception as e:
        print(f"ОШИБКА при записи в SQLite: {e}")
        sys.exit(1)

    # Верификация
    print("\nВерификация...")
    try:
        from db import load_db
        loaded = load_db()
        loaded_users = loaded.get("users") or {}
        loaded_chats = loaded.get("chats") or {}
        print(f"✓ Прочитано пользователей из SQLite: {len(loaded_users)}")
        print(f"✓ Прочитано чатов из SQLite: {len(loaded_chats)}")
        if len(loaded_users) != len(users):
            print(f"⚠ Расхождение пользователей: JSON={len(users)}, SQLite={len(loaded_users)}")
        else:
            print("✓ Количество пользователей совпадает")
    except Exception as e:
        print(f"ОШИБКА верификации: {e}")

    print("\n=== Миграция завершена ===")
    print("\nДля переключения бота на SQLite:")
    print("  1. Добавьте USE_SQLITE=true в переменные окружения сервиса")
    print(f"     Например: в /etc/systemd/system/telegram-political-monitor-bot.service")
    print(f"     добавьте строку: Environment=USE_SQLITE=true")
    print("  2. Перезапустите: systemctl daemon-reload && systemctl restart telegram-political-monitor-bot")
    print("\nJSON-файл остаётся нетронутым как резервная копия.")


if __name__ == "__main__":
    main()
