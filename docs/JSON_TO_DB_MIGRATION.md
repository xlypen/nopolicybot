# План миграции с JSON на БД

## Уже перенесено

| Источник | Куда | Статус |
|----------|------|--------|
| Социальный граф (связи, саммари, тон, топики) | `edges` | Чтение/запись из БД |
| Диалоги (сырые сообщения) | `messages` | Запись через `ingest_message_event`, чтение в `process_pending_days` |
| Обработанные даты (processed_dates) | Таблица `processed_dates` | Чтение/запись из БД, fallback на JSON |
| Участники чата, счётчики сообщений | `messages` + `users` | `get_users_in_chat`, `get_user_display_names`, `get_user` — приоритет БД |
| Переопределения настроек по чатам | `chat_settings` | Чтение/запись через `get_chat_overrides`, `set_chat_override` |

## В работе / следующий шаг

| Файл/модуль | Содержимое | Предложение |
|-------------|------------|-------------|
| **user_stats.json** | Ранги, political_messages, warnings, sentiment, архивы сообщений, флаги (question_of_day, factcheck, portrait и т.д.) | Таблица `user_stats` или расширение `users`: добавить колонки/JSON для ранга, счётчиков, флагов; архивы сообщений не хранить в БД (уже есть в `messages`) или вынести в отдельную таблицу |
| **bot_settings.json** | Глобальные настройки (DEFAULTS) | Вариант 1: одна строка в таблице `global_settings` (key-value или один JSON). Вариант 2: оставить файл/env для глобальных настроек, в БД только переопределения по чатам (уже сделано) |
| **storage_mode.json** | Режим хранения (db_only, dual и т.д.) | Перевести на переменную окружения `STORAGE_MODE` |
| **decision_events.json** | События решений движка | Таблица `decision_events` (id, created_at, payload JSON) |
| **marketing_metrics.json** | Метрики маркетинга | Таблица `marketing_metrics` или отдельная БД/сервис |
| **question_of_day_*.json**, **reset_political_count.json** | Состояние вопросов дня и сбросов | Таблицы или одна таблица `bot_state` (key, value JSON) |
| **bot_last_start.json**, **bot_state.json**, **bot_explainability.json** | Время старта, состояние, объяснимость | Та же `bot_state` или отдельные маленькие таблицы |
| **social_graph.json** | `last_processed`, `realtime_cursors`, fallback для connections/dialogue_log | `last_processed` и курсоры можно хранить в таблице `social_graph_state` (key-value); связи уже в `edges`, диалоги в `messages` |

## Рекомендуемый порядок

1. **storage_mode** → env (минимальные изменения).
2. **bot_settings** глобальные → либо оставить JSON, либо одна запись в БД.
3. **user_stats** расширение → добавить в БД поля для ранга, political_messages, warnings, sentiment; чтение/запись через репозиторий с fallback на JSON.
4. **decision_events**, **marketing_metrics** → отдельные таблицы при необходимости масштабирования.
5. Мелкие state-файлы (**question_of_day**, **reset_political_count**, **bot_last_start**, **bot_state**) → таблица `bot_state` (key, value, updated_at).

После переноса каждого блока: отключить запись в JSON (или оставить dual-write на переходный период), затем убрать чтение из JSON.
