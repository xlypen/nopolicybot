#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Создаю виртуальное окружение и ставлю зависимости..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi

if ! grep -q "вставьте" .env 2>/dev/null; then
    echo "Запуск бота..."
    ./venv/bin/python bot.py
else
    echo "Сначала отредактируйте .env: вставьте TELEGRAM_BOT_TOKEN и OPENAI_API_KEY"
    exit 1
fi
