#!/bin/bash
# Установка бота и админки на Ubuntu. Запуск из корня проекта: ./deploy-ubuntu/install.sh
# Или: ./deploy-ubuntu/install.sh /opt/telegram-political-monitor-bot

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${1:-$(dirname "$SCRIPT_DIR")}"
LOG_FILE="$SCRIPT_DIR/install.log"
PIP_RETRIES=3
PIP_RETRY_DELAY=10

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== Установка: проект $PROJECT_DIR ==="

if [ ! -f "$PROJECT_DIR/bot.py" ] || [ ! -f "$PROJECT_DIR/admin_app.py" ]; then
  log "Ошибка: в $PROJECT_DIR не найдены bot.py или admin_app.py. Укажите путь к проекту: $0 /path/to/project"
  exit 1
fi

# Системные пакеты
log "Шаг 1/6: установка системных пакетов (apt)..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq 2>> "$LOG_FILE"
sudo apt-get install -y -qq python3 python3-pip python3-venv nginx 2>> "$LOG_FILE" || {
  log "Ретрай apt..."
  sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv nginx
}
log "Системные пакеты установлены."

# Виртуальное окружение
log "Шаг 2/6: создание venv..."
cd "$PROJECT_DIR"
if [ ! -d "venv" ]; then
  python3 -m venv venv
  log "venv создан."
else
  log "venv уже есть."
fi

# pip install с ретраями
REQ_FILE="$SCRIPT_DIR/requirements-deploy.txt"
if [ ! -f "$REQ_FILE" ]; then
  REQ_FILE="$PROJECT_DIR/requirements.txt"
  log "Используется $REQ_FILE"
fi

log "Шаг 3/6: установка Python-зависимостей (до $PIP_RETRIES попыток)..."
. venv/bin/activate
for i in $(seq 1 "$PIP_RETRIES"); do
  log "Попытка pip install №$i..."
  if pip install -r "$REQ_FILE" -q 2>> "$LOG_FILE" && pip install gunicorn -q 2>> "$LOG_FILE"; then
    log "pip install успешен."
    break
  fi
  if [ "$i" -eq "$PIP_RETRIES" ]; then
    log "Ошибка: pip install не удался после $PIP_RETRIES попыток. См. $LOG_FILE"
    exit 1
  fi
  log "Ждём ${PIP_RETRY_DELAY} сек перед повтором..."
  sleep "$PIP_RETRY_DELAY"
done
deactivate 2>/dev/null || true
log "Зависимости установлены."

# .env из шаблона
log "Шаг 4/6: проверка .env..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
  if [ -f "$SCRIPT_DIR/env.template" ]; then
    cp "$SCRIPT_DIR/env.template" "$PROJECT_DIR/.env"
    log "Создан .env из env.template. Отредактируйте его: nano $PROJECT_DIR/.env"
  else
    log "Файл env.template не найден. Создайте .env вручную."
  fi
else
  log ".env уже существует."
fi

# systemd
log "Шаг 5/6: установка systemd-сервисов..."
cat > /tmp/telegram-bot.service << EOF
[Unit]
Description=Telegram Political Monitor Bot
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

cat > /tmp/telegram-bot-admin.service << EOF
[Unit]
Description=Telegram Bot Admin (Gunicorn)
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 --timeout 120 admin_app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo cp /tmp/telegram-bot.service /etc/systemd/system/
sudo cp /tmp/telegram-bot-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot telegram-bot-admin
log "Сервисы включены (start — после настройки .env)."

# Nginx
log "Шаг 6/6: настройка nginx..."
sudo tee /etc/nginx/sites-available/telegram-bot > /dev/null << 'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/telegram-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
sudo nginx -t 2>> "$LOG_FILE" && sudo systemctl reload nginx
log "Nginx настроен."

log "=== Установка завершена. Лог: $LOG_FILE ==="
echo ""
echo "Дальше:"
echo "  1. Отредактируйте .env: nano $PROJECT_DIR/.env"
echo "  2. Запустите сервисы: sudo systemctl start telegram-bot telegram-bot-admin"
echo "  3. Проверка: sudo systemctl status telegram-bot telegram-bot-admin"
echo "  4. Сайт: http://$(curl -s ifconfig.me 2>/dev/null || echo 'ВАШ_IP')/"
