# PostgreSQL как основная БД

## Как подключается приложение

1. Читается `.env` (`load_dotenv`).
2. Вызывается `config.database_url.materialize_database_url_env()`:
   - если заданы **`POSTGRES_HOST`**, **`POSTGRES_DB`**, **`POSTGRES_USER`** (и при необходимости пароль),
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
