# Рейтинг участников в админке

**Источник данных (режим `dual` / `db_first` / `db_only`):** таблицы **`messages`** и **`marketing_signal_events`** в PostgreSQL/SQLite. Агрегация в `services/marketing_metrics.py` + `services/marketing_metrics_db.py` (синхронный SQLAlchemy, без опоры на JSON для расчётов).

- Сообщения и ответы попадают в БД через **`ingest_message_event`** (`services/db_ingest.py`), в т.ч. поле **`mention_user_ids`**.
- После анализа ИИ тональность/политика пишется в **`marketing_signal_events`** через **`record_signal_event`** (в `db_only` без дублирования в JSON).

**Режим без БД (редко):** при отключённом `storage_db_reads_enabled()` по-прежнему можно использовать **`data/marketing_metrics.json`** и `record_message_event` для накопления `user_daily`.

Окно отчёта на дашборде по умолчанию **30 дней** (параметр `days` в API).

## Метрики (выпадающий список «Метрика»)

Все значения приводятся к диапазону **0…1** (`_clamp01`), кроме того что `churn_risk` трактуется как риск оттока.

### Вовлечённость (`engagement_score`)

Из агрегатов за окно:

- `reply_rate` = ответы / сообщения  
- `mention_frequency` = (упоминания к вам + 0.5 × ваши упоминания) / сообщения  
- `response_time_factor` = штраф за среднее время ответа в треде (до 1 ч как ориентир)  
- `discussion_depth` = (ответы отправленные + полученные) / сообщения  

**Формула:**  
`0.3×reply_rate + 0.2×mention_frequency + 0.2×response_time_factor + 0.3×discussion_depth`

### Влияние (`influence_score`)

Используется **текущий граф связей** чата (pagerank/reach из `build_graph_payload`, кэшируется на процесс для скорости):

- `pagerank` — нормированный  
- `reach_factor` — упоминания и входящие ответы к активности  
- плюс доли `reply_rate` и `sentiment_shift` (сдвиг +/−/нейтраль в окне)

**Формула:**  
`0.4×pagerank + 0.3×reach + 0.2×reply_rate + 0.1×sentiment_shift`

### Удержание (`retention_score`)

- доля активных дней в окне  
- серия подряд активных дней (до 7 как ориентир)  
- свежесть последней активности (до ~14 дней)  
- «качество контента» = меньше доля негативных меток в окне  

### Виральность (`viral_coefficient`)

`mentions_received / max(1, messages)` — насколько человека тегают относительно его сообщений.

### Риск оттока (`churn_risk`)

`1 − retention_score` (чем ниже удержание, тем выше риск). В топе списка при сортировке по этой метрике — **наибольший** риск.

## Колонка «Тренд»

Сравниваются **два одинаковых по длине периода**:

- **текущий** счёт: последние `N` дней (как у виджета, напр. 30)  
- **предыдущий**: те же `N` дней, но **сдвинутые на `N` назад** (`window_end_offset_days=N`)

Разница `current − previous`; если |Δ| &lt; 0.005 — показывается «плоско» (→).

Граф связей для обоих окон берётся **текущий** (для влияния тренд ориентировочный).

## Актуальность

- Сырые события пишутся ботом **сразу** в JSON (при включённых JSON-записях).  
- Виджеты API кэшируются ~**25 с** (`ADMIN_DASHBOARD_CACHE_TTL_SEC`); смена чата / обновление страницы с `refresh=1` даёт пересчёт без кэша.  
- Кэш графа для pagerank — **LRU до 64 чатов** в процессе API (после смены топологии может быть до следующего промаха кэша).

## Файлы кода

- `services/marketing_metrics.py` — `get_user_metrics`, `get_leaderboard`  
- `services/admin_dashboards.py` — `build_user_leaderboard_dashboard`  
- `api/routers/admin.py` — `GET /api/v2/admin/leaderboard`
