# OOM и слабый VPS (1 vCPU, 2 GB RAM)

На одной машине часто крутятся **PostgreSQL**, **telegram-bot**, **Gunicorn (админка)** и **Uvicorn (API)**. Пики по дашборду/графу легко приводят к **Out Of Memory**.

## Переменные `.env`

```env
API_WORKERS=1
LOW_MEMORY_SERVER=1
API_WORKERS_MAX=4

# Если async engine использует пул (PostgreSQL):
# DB_POOL_SIZE=3
# DB_MAX_OVERFLOW=5
```

Перезапуск: `sudo systemctl restart telegram-bot-api telegram-bot-admin telegram-bot`

## Gunicorn (админка)

В `deploy-ubuntu/telegram-bot-admin.service` для слабого сервера: **`-w 1`**, меньше `--worker-connections` (например 30).

## PostgreSQL на том же хосте

Ориентир для **2 GB** всего сервера: `shared_buffers` 128–256MB, `work_mem` 4MB, умеренный `max_connections`.

## Swap / zram

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

Или zram (Ubuntu: `zram-config`). Swap не заменяет RAM, но снижает частоту OOM-killer.

## Диагностика

```bash
journalctl -k | grep -i 'out of memory\|oom'
free -h
ps aux --sort=-%mem | head -15
```

Рекомендация: **≥ 4 GB RAM** или вынести Postgres отдельно.
