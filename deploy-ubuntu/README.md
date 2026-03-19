# Развёртывание на Ubuntu

Готовый установочный набор для бота и админ-панели на Ubuntu. Текущий проект не меняется — используются только файлы из этой папки и корня проекта.

## Минимальная переноска на сервер

Чтобы не тащить весь репозиторий (venv, тесты, кэш и т.п.), собери минимальный архив:

- **Windows (PowerShell):** из корня проекта запусти  
  `.\deploy-ubuntu\pack.ps1`  
  Получится `deploy-ubuntu\telegram-bot-minimal.zip`.
- **Linux / Mac / Git Bash:**  
  `./deploy-ubuntu/pack.sh`  
  Получится `deploy-ubuntu/telegram-bot-minimal.tar.gz`.

В архив попадают только файлы из `MANIFEST.txt`: код бота и админки, шаблоны, статика и сама папка `deploy-ubuntu`. Без venv, без тестов, без данных и секретов.

Перенеси на сервер один файл (`telegram-bot-minimal.zip` или `.tar.gz`), распакуй в `/opt`, затем запусти установку (см. ниже).

## Что внутри

| Файл | Назначение |
|------|------------|
| `install.sh` | Основной скрипт: apt, venv, pip (с ретраями и логом), .env из шаблона, systemd, nginx |
| `pack.sh` / `pack.ps1` | Сборка минимального архива по MANIFEST.txt |
| `MANIFEST.txt` | Список файлов для минимального архива |
| `env.template` | Шаблон переменных окружения (копируется в `.env` при первом запуске) |
| `requirements-deploy.txt` | Минимальные зависимости (без torch/diffusers и тестов) |
| `telegram-bot.service` | Пример unit для бота (путь подставит install.sh) |
| `telegram-bot-admin.service` | Пример unit для админки (Gunicorn) |
| `install.log` | Появляется после запуска — подробный лог установки |

## Как ставить

1. Перенеси на сервер **минимальный архив** (см. выше) или весь проект. Распакуй в `/opt`, например:
   ```bash
   cd /opt
   unzip telegram-bot-minimal.zip   # или: tar -xzf telegram-bot-minimal.tar.gz
   mv telegram-bot-minimal telegram-political-monitor-bot
   cd telegram-political-monitor-bot
   ```

2. Запусти установку (лучше от root или с sudo):
   ```bash
   chmod +x deploy-ubuntu/install.sh
   sudo ./deploy-ubuntu/install.sh
   ```
   Или с явным путём к проекту:
   ```bash
   sudo ./deploy-ubuntu/install.sh /opt/telegram-political-monitor-bot
   ```

3. Отредактируй `.env` в корне проекта (токен бота, API-ключи, `PARTICIPANT_BASE_URL`):
   ```bash
   nano /opt/telegram-political-monitor-bot/.env
   ```

4. Запусти сервисы:
   ```bash
   sudo systemctl start telegram-bot telegram-bot-admin
   sudo systemctl status telegram-bot telegram-bot-admin
   ```

5. Открой в браузере: `http://IP_СЕРВЕРА/` — главная; `/login` — вход в админку (пароль задаётся при первом заходе или в `ADMIN_PASSWORD`).

## Переменные в env.template

- **TELEGRAM_BOT_TOKEN** — токен от @BotFather (обязательно).
- **OPENAI_API_KEY**, **OPENAI_BASE_URL**, **OPENAI_MODEL** — для ИИ (обязательно для работы бота).
- **ADMIN_SECRET_KEY** — произвольная строка для сессий админки.
- **PARTICIPANT_BASE_URL** — URL сайта для ссылок «Мой профиль» (например `http://80.66.87.114` или `https://bot.example.com`).
- **ADMIN_PASSWORD** — опционально; если не задан, пароль задаётся при первом заходе на `/login`.

Остальные переменные в шаблоне опциональны (голос, портреты, модели и т.д.).

## Лог и ретраи

- Подробный лог установки: `deploy-ubuntu/install.log`.
- `pip install` выполняется до 3 раз с паузой 10 сек при ошибке.

## После обновления кода

```bash
cd /opt/telegram-political-monitor-bot
sudo systemctl restart telegram-bot telegram-bot-admin
```
