#!/bin/bash
# Быстрое исправление 404 для /api/v2/personality/*
# Запуск на сервере из корня проекта: ./scripts/fix_nginx_personality.sh
# Или: bash scripts/fix_nginx_personality.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Шаг 1: Проверка текущего nginx ==="
if [ -f /etc/nginx/sites-enabled/nopolicybot ]; then
  echo "Текущий блок personality:"
  grep -A5 "personality" /etc/nginx/sites-enabled/nopolicybot 2>/dev/null || echo "(блок не найден)"
else
  echo "Файл /etc/nginx/sites-enabled/nopolicybot не найден."
  echo "Проверяю sites-enabled:"
  ls -la /etc/nginx/sites-enabled/ 2>/dev/null || true
fi

echo ""
echo "=== Шаг 2: Копирование канонического конфига ==="
echo "Источник: $PROJECT_DIR/nginx/nopolicybot.conf"
if [ ! -f "$PROJECT_DIR/nginx/nopolicybot.conf" ]; then
  echo "ОШИБКА: $PROJECT_DIR/nginx/nopolicybot.conf не найден"
  exit 1
fi

sudo cp "$PROJECT_DIR/nginx/nopolicybot.conf" /etc/nginx/sites-available/nopolicybot
sudo ln -sf /etc/nginx/sites-available/nopolicybot /etc/nginx/sites-enabled/nopolicybot
sudo rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/telegram-bot 2>/dev/null || true

echo "Конфиг скопирован. Проверка personality:"
grep -A3 "personality" /etc/nginx/sites-available/nopolicybot

echo ""
echo "=== Шаг 3: Проверка синтаксиса и перезагрузка ==="
sudo nginx -t && sudo systemctl reload nginx
echo "Nginx перезагружен."

echo ""
echo "=== Шаг 4: Перезапуск Flask admin (если нужно) ==="
if systemctl is-active --quiet telegram-bot-admin 2>/dev/null; then
  sudo systemctl restart telegram-bot-admin
  echo "telegram-bot-admin перезапущен."
else
  echo "Сервис telegram-bot-admin не найден — перезапустите gunicorn/admin вручную."
fi

echo ""
echo "=== Готово ==="
echo "Проверка: curl -I http://127.0.0.1/api/v2/personality/user/481864720?chat_id=-1001758892482"
echo "(должен вернуть 302 redirect на /login без сессии, а не 404)"
