# PostgreSQL как основная БД

## Docker (compose в корне репозитория)

Файл **`docker-compose.yml`** поднимает сервис **`postgres`** с теми же переменными, что ожидает бот (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`). Значения по умолчанию в compose: пользователь **`postgres`**, пароль **`postgres`**, БД **`nopolicybot`** — их можно переопределить в `.env`.

### Где «лежит» DATABASE_URL

Отдельного секрета в коде нет: строка **всегда** собирается из переменных (или задаётся явно в `.env`).

- **Бот на хосте** (как у вас с systemd), Postgres в Docker с пробросом порта **`5432:5432`**:
  - в `.env` те же `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`, что задали контейнеру;
  - **`POSTGRES_HOST=127.0.0.1`**, **`POSTGRES_PORT=5432`** (или ваш `POSTGRES_PUBLISH_PORT`, если меняли публикацию).

Эквивалентная явная строка (подставьте свои значения, пароль — URL-encoded при спецсимволах):

```text
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/nopolicybot
```

- **Бот в том же `docker compose`** (отдельный сервис в одной сети): хост БД — имя сервиса, **`POSTGRES_HOST=postgres`**, порт **`5432`**.

После старта контейнера: миграции и проверка — `scripts/apply_marketing_metrics_migration.py`, `scripts/pg_quick_status.py`.

## Как подключается приложение

1. Читается `.env` (`load_dotenv`).
2. Вызывается `config.database_url.materialize_database_url_env()`:
   - если заданы **`POSTGRES_DB`**, **`POSTGRES_USER`** (и при необходимости пароль), плюс **`POSTGRES_HOST`** или пустой хост → тогда **`127.0.0.1`**; можно вместо префикса `POSTGRES_` использовать **`PGHOST`**, **`PGDATABASE`**, **`PGUSER`**, **`PGPASSWORD`**, **`PGPORT`**,
     в **`DATABASE_URL`** подставляется `postgresql+asyncpg://…`, **даже если в .env осталась строка sqlite**;
   - если в `.env` уже указан **`postgresql+…`** / **`postgres://`**, он **не перезаписывается**.
3. `db/engine.py` создаёт async SQLAlchemy engine на итоговом `DATABASE_URL`.

Точки входа, где это учтено: **`db/engine.py`** (при любом импорте), **`bot.py`**, **`admin_app.py`**, **`api/main.py`**.

## Что хранится в Postgres

Таблицы из `db/models.py`: `users`, `messages`, `edges`, `chat_settings`, `user_profiles`, `personality_profiles`, и т.д.

## Второй файл `data/bot.db` (SqliteStorage)

Слой `services/sqlite_storage.py` при включённом режиме хранения в БД может по-прежнему использовать **отдельный** SQLite-файл для части legacy-данных (настройки, копии профилей). Полный перенос этого слоя в те же таблицы Postgres — отдельная задача. Режим **`STORAGE_MODE=db_only`** и основной **`DATABASE_URL` на Postgres** — обязательный минимум для прода.

## Миграции схемы

```bash
.venv/bin/python scripts/apply_marketing_metrics_migration.py
.venv/bin/python scripts/pg_quick_status.py
```
