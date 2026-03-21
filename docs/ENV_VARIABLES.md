# Переменные окружения

Сводка по `os.getenv` / `load_dotenv` в репозитории. Значения **не** коммитьте: используйте `.env` (в `.gitignore`).

## Обязательные по сервису (`config/validate_secrets.py`)

| Сервис | Переменные |
|--------|------------|
| **bot** | `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL` |
| **admin** | `ADMIN_SECRET_KEY`, `DATABASE_URL` |
| **api** | `ADMIN_TOKEN`, `DATABASE_URL` |

Для PostgreSQL в `DATABASE_URL` должен быть **пароль** (для SQLite — не требуется).

## Секреты и доступ

| Переменная | Назначение |
|------------|------------|
| `TELEGRAM_BOT_TOKEN` | Токен бота (@BotFather) |
| `OPENAI_API_KEY` | OpenAI / OpenRouter / совместимые API |
| `OPENAI_BASE_URL` | Базовый URL API (например OpenRouter) |
| `ADMIN_TOKEN` | Bearer для защищённых эндпоинтов FastAPI v2 |
| `ADMIN_SECRET_KEY` | Секрет сессий Flask и fallback для подписи ссылок |
| `ADMIN_PASSWORD` | Опционально: пароль админки (неинтерактивный деплой) |
| `PARTICIPANT_SECRET` | Опционально: отдельный секрет для ссылок участника |
| `PARTICIPANT_BASE_URL`, `ADMIN_BASE_URL` | Базовый URL для ссылок (`bot.py`, `admin_app.py`) |
| `USER_HASH_SALT` | Опционально: соль хеширования пользователей (`data_privacy`) |
| `SECRETS_VALIDATE` | `0`/`false` — отключить валидацию секретов при старте (только отладка) |

## База данных

| Переменная | Назначение |
|------------|------------|
| `DATABASE_URL` | DSN. Рекомендуется `postgresql+asyncpg://...`. SQLite остаётся для локальной отладки без Postgres. |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Если заданы (вместе с БД и пользователем), `materialize_database_url_env()` **подставляет** `DATABASE_URL` на Postgres и **заменяет** строку sqlite в `.env`. Уже заданный в `.env` `postgresql+…` не трогается. |
| `POSTGRES_DRIVER` | По умолчанию `asyncpg` |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | Пул SQLAlchemy (`db/engine.py`) |

**CLI только для PostgreSQL** (`scripts/apply_marketing_metrics_migration.py`, `scripts/pg_quick_status.py`): URL берётся как `postgresql*` из `DATABASE_URL`, **либо** собирается из `POSTGRES_*`, даже если в `DATABASE_URL` остался sqlite (см. `config.database_url.postgres_url_for_cli_scripts`).

## Хранилище (JSON ↔ БД)

| Переменная | Назначение |
|------------|------------|
| `STORAGE_MODE` | `json` \| `dual` \| `db_first` \| `db_only` (алиасы: `hybrid`→`dual`, `db`→`db_only`) |
| `STORAGE_PRIMARY` | Синоним `STORAGE_MODE` |
| `PARITY_CHECK_INTERVAL_SEC` | Интервал проверки паритета JSON/БД |
| `MESSAGE_RETENTION_DAYS`, `RETENTION_CHECK_INTERVAL_SEC` | Ретеншн сообщений (`schedulers`) |

Режим **`dual`** — параллельная запись в JSON и БД (переходный). После миграции обычно **`db_only`**.

## HTTP API (FastAPI, `api/main.py`, `run_api.py`)

| Переменная | Назначение |
|------------|------------|
| `API_PORT`, `API_WORKERS`, `API_WORKERS_MAX` | Порт и воркеры Uvicorn |
| `LOW_MEMORY_SERVER` | `1` — урезать воркеры на слабом VPS |
| `API_LOG_LEVEL`, `API_RELOAD` | Лог и hot-reload |
| `ALLOWED_ORIGINS` | CORS (список через запятую) |
| `API_RATE_LIMIT_PER_MIN`, `API_MAX_URL_LENGTH`, `API_MAX_BODY_BYTES` | Лимиты API |

## Админка Flask (`admin_app.py`)

| Переменная | Назначение |
|------------|------------|
| `ADMIN_HOST`, `ADMIN_PORT` | Bind админ-панели |
| `FLASK_RATE_LIMIT_PER_MIN`, `FLASK_MAX_URL_LENGTH`, `FLASK_MAX_BODY_BYTES` | Лимиты Flask |

## Прокси к API (`utils/fastapi_proxy.py`)

Использует `API_PORT`, `ADMIN_TOKEN`.

## ИИ (`ai/client.py`, `ai_analyzer.py`, сервисы personality / chat)

| Переменная | Назначение |
|------------|------------|
| `OPENAI_MODEL` | Основная модель чата/анализа |
| `OPENAI_FACTCHECK_MODEL` | Модель для фактчека |
| `OPENAI_VISION_MODEL`, `OPENAI_VISION_MODELS` | Видео/картинки |
| `OPENAI_REPLY_MODELS` | Список моделей для ответов |
| `GEMINI_MODEL` | Модель Gemini |
| `GEMINI_API_KEY`, `GOOGLE_API_KEY` | Ключи Google AI |
| `AI_USE_OPENROUTER_FIRST`, `AI_PREFER_FREE`, `AI_USE_GEMINI_FIRST` | Порядок выбора провайдера |
| `AI_FAST_CACHE_TTL_SEC`, `AI_FAST_CACHE_MAX_ITEMS` | Кэш быстрых ответов |
| `PERSONALITY_ENSEMBLE_MODELS` | Ансамбль моделей личности |

## Картинки / портреты

| Переменная | Назначение |
|------------|------------|
| `HF_TOKEN`, `HUGGINGFACE_TOKEN` | HuggingFace Inference |
| `REPLICATE_API_TOKEN` | Replicate |
| `OPENAI_API_KEY` | OpenRouter и т.д. в цепочке image_generator |

## Realtime / Redis (`services/realtime_broadcast.py`)

| Переменная | Назначение |
|------------|------------|
| `REDIS_URL` | Подписка на события для WS |
| `WS_REDIS_CHANNEL_PREFIX` | Префикс канала (по умолчанию `nopolicybot:ws`) |

## Прочее

| Переменная | Назначение |
|------------|------------|
| `TELEGRAM_BOT_TOKEN` | Также `services/graph_api.py` |
| `SLO_AUTH_TOKEN` | `scripts/check_slo_gate.py` |

## Внутренние / тесты

- `PYTEST_CURRENT_TEST` — выставляется pytest.
- `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` — могут задаваться в `ai/client.py` для кастомного CA.

## Полный алфавитный список имён (по коду)

```
ADMIN_BASE_URL
ADMIN_HOST
ADMIN_PASSWORD
ADMIN_PORT
ADMIN_SECRET_KEY
ADMIN_TOKEN
AI_FAST_CACHE_MAX_ITEMS
AI_FAST_CACHE_TTL_SEC
AI_PREFER_FREE
AI_USE_GEMINI_FIRST
AI_USE_OPENROUTER_FIRST
ALLOWED_ORIGINS
API_LOG_LEVEL
API_MAX_BODY_BYTES
API_MAX_URL_LENGTH
API_PORT
API_RATE_LIMIT_PER_MIN
API_RELOAD
API_WORKERS
API_WORKERS_MAX
DATABASE_URL
DB_MAX_OVERFLOW
DB_POOL_SIZE
FLASK_MAX_BODY_BYTES
FLASK_MAX_URL_LENGTH
FLASK_RATE_LIMIT_PER_MIN
GEMINI_API_KEY
GEMINI_MODEL
GOOGLE_API_KEY
HF_TOKEN
HUGGINGFACE_TOKEN
LOW_MEMORY_SERVER
MESSAGE_RETENTION_DAYS
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_FACTCHECK_MODEL
OPENAI_MODEL
OPENAI_REPLY_MODELS
OPENAI_VISION_MODEL
OPENAI_VISION_MODELS
PARTICIPANT_BASE_URL
PARTICIPANT_SECRET
PARITY_CHECK_INTERVAL_SEC
PERSONALITY_ENSEMBLE_MODELS
POSTGRES_DB
POSTGRES_DRIVER
POSTGRES_HOST
POSTGRES_PASSWORD
POSTGRES_PORT
POSTGRES_USER
PYTEST_CURRENT_TEST
REDIS_URL
REPLICATE_API_TOKEN
RETENTION_CHECK_INTERVAL_SEC
SECRETS_VALIDATE
SLO_AUTH_TOKEN
STORAGE_MODE
STORAGE_PRIMARY
TELEGRAM_BOT_TOKEN
USER_HASH_SALT
WS_REDIS_CHANNEL_PREFIX
```

Обновляйте этот файл при добавлении новых `getenv` в проект.
