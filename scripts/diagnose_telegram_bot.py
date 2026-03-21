#!/usr/bin/env python3
"""
Диагностика: почему бот «не читает» (нет апдейтов при polling).

Запуск с сервера, где лежит .env:
  cd /opt/telegram-political-monitor-bot && ./venv/bin/python scripts/diagnose_telegram_bot.py

Проверяет: getMe, getWebhookInfo, validate_secrets для bot (как при старте bot.py).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env", encoding="utf-8-sig", override=True)
    except ImportError:
        pass

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN не задан в окружении / .env")
        return 1

    base = f"https://api.telegram.org/bot{token}"

    def call(method: str) -> dict:
        url = f"{base}/{method}"
        req = Request(url, method="GET")
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    print("--- getMe ---")
    try:
        me = call("getMe")
        print(json.dumps(me, indent=2, ensure_ascii=False))
        if not me.get("ok"):
            print("Токен отклонён Telegram — бот не сможет работать.")
            return 1
    except HTTPError as e:
        print(f"HTTP ошибка getMe: {e.code} {e.reason}")
        return 1
    except URLError as e:
        print(f"Сеть недоступна (getMe): {e}")
        return 1

    print("\n--- getWebhookInfo ---")
    try:
        wh = call("getWebhookInfo")
        print(json.dumps(wh, indent=2, ensure_ascii=False))
        res = wh.get("result") or {}
        url_wh = (res.get("url") or "").strip()
        if url_wh:
            print(
                "\n>>> ПРОБЛЕМА: на боте настроен WEBHOOK. bot.py использует polling — "
                "апдейты уходят на URL вебхука, getUpdates пустой. Бот «молчит».\n"
                "    Сброс (выполните на сервере, подставьте токен):\n"
                '    curl -sS "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/deleteWebhook?drop_pending_updates=true"\n'
                "    или в BotFather / через админку, если настраивали вручную."
            )
        pending = int(res.get("pending_update_count") or 0)
        if pending > 0 and not url_wh:
            print(f"\nОжидающих апдейтов в очереди Telegram: {pending} (после запуска polling разберутся).")
    except Exception as e:
        print(f"getWebhookInfo failed: {e}")

    print("\n--- validate_secrets('bot') как при старте bot.py ---")
    try:
        from config.validate_secrets import validate_secrets

        validate_secrets("bot", force=True)
        print("OK — секреты для бота проходят проверку.")
    except Exception as e:
        print(f"ОШИБКА: {e}")
        print("Пока это не исправить, процесс bot.py падает при старте и ничего не читает.")
        return 1

    print(
        "\n--- Дальше вручную ---\n"
        "• systemctl status telegram-bot.service  (или ваш юнит)\n"
        "• journalctl -u telegram-bot.service -n 80 --no-pager\n"
        "• Убедитесь, что не запущено ДВА процесса с одним токеном (будет 409 / пропуск апдейтов).\n"
        "• В группах: BotFather → Group Privacy off, иначе бот не видит обычные сообщения.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
