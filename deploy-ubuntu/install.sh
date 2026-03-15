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
log "Шаг 1/7: установка системных пакетов (apt)..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq 2>> "$LOG_FILE"
sudo apt-get install -y -qq python3 python3-pip python3-venv nginx 2>> "$LOG_FILE" || {
  log "Ретрай apt..."
  sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv nginx
}
log "Системные пакеты установлены."

# Виртуальное окружение
log "Шаг 2/7: создание venv..."
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

log "Шаг 3/7: установка Python-зависимостей (до $PIP_RETRIES попыток)..."
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
log "Шаг 4/7: проверка .env..."
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

# system user + права
log "Шаг 5/7: подготовка системного пользователя и прав..."
if ! id -u nopolicybot >/dev/null 2>&1; then
  sudo useradd -r -s /usr/sbin/nologin -d "$PROJECT_DIR" nopolicybot 2>> "$LOG_FILE" || \
  sudo useradd -r -s /bin/false -d "$PROJECT_DIR" nopolicybot 2>> "$LOG_FILE"
fi
sudo mkdir -p "$PROJECT_DIR/data"
sudo chown -R nopolicybot:nopolicybot "$PROJECT_DIR"
log "Пользователь nopolicybot и права каталога готовы."

# systemd
log "Шаг 6/7: установка systemd-сервисов..."
cat > /tmp/telegram-bot.service << EOF
[Unit]
Description=Telegram Political Monitor Bot
After=network.target

[Service]
Type=simple
User=nopolicybot
Group=nopolicybot
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/bot.py
NoNewPrivileges=true
PrivateTmp=true
ReadWritePaths=$PROJECT_DIR/data
Restart=on-failure
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
User=nopolicybot
Group=nopolicybot
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=/bin/bash -lc 'CPU="$$(nproc 2>/dev/null || echo 1)"; W="$$((2 * CPU + 1))"; if [ "$$W" -lt 3 ]; then W=3; fi; exec $PROJECT_DIR/venv/bin/gunicorn -w "$$W" -k gevent --worker-connections 50 -b 127.0.0.1:5000 --timeout 30 --keep-alive 5 --max-requests 1000 --max-requests-jitter 100 admin_app:app'
NoNewPrivileges=true
PrivateTmp=true
ReadWritePaths=$PROJECT_DIR/data
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /tmp/telegram-bot-api.service << EOF
[Unit]
Description=Telegram Bot FastAPI (Uvicorn)
After=network.target

[Service]
Type=simple
User=nopolicybot
Group=nopolicybot
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/run_api.py
NoNewPrivileges=true
PrivateTmp=true
ReadWritePaths=$PROJECT_DIR/data
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo cp /tmp/telegram-bot.service /etc/systemd/system/
sudo cp /tmp/telegram-bot-admin.service /etc/systemd/system/
sudo cp /tmp/telegram-bot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot telegram-bot-admin telegram-bot-api
log "Сервисы включены (start — после настройки .env)."

# Nginx (канонический конфиг из nginx/nopolicybot.conf)
log "Шаг 7/7: настройка nginx..."
sudo cp "$PROJECT_DIR/nginx/nopolicybot.conf" /etc/nginx/sites-available/nopolicybot
sudo rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/telegram-bot 2>/dev/null || true
sudo ln -sf /etc/nginx/sites-available/nopolicybot /etc/nginx/sites-enabled/nopolicybot
sudo nginx -t 2>> "$LOG_FILE" && sudo systemctl reload nginx
log "Nginx настроен."

log "=== Установка завершена. Лог: $LOG_FILE ==="
echo ""
echo "Дальше:"
echo "  1. Отредактируйте .env: nano $PROJECT_DIR/.env"
echo "  2. Запустите сервисы: sudo systemctl start telegram-bot telegram-bot-admin telegram-bot-api"
echo "  3. Проверка: sudo systemctl status telegram-bot telegram-bot-admin telegram-bot-api"
echo "  4. Сайт: http://$(curl -s ifconfig.me 2>/dev/null || echo 'ВАШ_IP')/"
