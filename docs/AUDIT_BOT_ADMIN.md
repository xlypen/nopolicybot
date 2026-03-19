# Аудит бота и админки: узкие места и рекомендации

Дата: 2025-03.

## 1. Бот (bot.py)

### Узкие места
- **Sync file I/O на event loop**: вызовы `user_stats.*`, `bot_settings.get*`, `bot_state.save_state` выполняются синхронно в обработчиках и блокируют цикл на время чтения/записи JSON.
- **get_me() на каждое сообщение**: фильтр `IsDirectedAtBotFilter` вызывал `bot.get_me()` для каждого сообщения с текстом/фото/голосом в группах. **Исправлено**: кэш на 2 мин.
- **Один поток для AI/голоса**: все тяжёлые задачи (AI, Whisper) идут в общий executor — одна долгая задача задерживает остальные.
- **Семафор ingest = 1**: запись в БД (ingest_message_event) сериализована, при всплеске сообщений возможна очередь.

### Риски
- Сообщения в группах **без** текста/фото/голоса (стикер, гифка, документ) не обрабатываются ни одним хендлером → «Update is not handled».
- Разные пороги «старости»: 180 с для групп (нет реакции), 600 с для лички (нет ответа) — легко перепутать при правках.

### Путаница
- `reply_to_bot_enabled` влияет только на ответы «к боту» (личка, reply, @mention). Реакции в группах идут через `check_and_reply` независимо.
- Пауза (`_dm_silence_until`) только в личке; в группах — свои флаги (moderation, api_interval, style и т.д.).

---

## 2. Админка (Flask + FastAPI)

### Поток запросов
- Браузер → nginx → **Flask** (сессия, HTML) → для `/api/v2/*` Flask проксирует в **FastAPI** (127.0.0.1:8001) через `proxy_to_fastapi` (sync HTTP, timeout 300 с).
- Часть путей (health, WS) может идти в FastAPI напрямую через nginx.

### Узкие места
- **Тяжёлые сборщики без кэша (раньше)**: `build_chat_health_dashboard`, `build_community_structure_dashboard`, `build_user_leaderboard_dashboard`, `build_at_risk_users_dashboard` — много вызовов `get_chat_health`/`get_user_metrics` (JSON), граф, предсказания. **Смягчено**: кэш в FastAPI 45 с, уменьшено число чатов в _aggregate_health (12 вместо 40).
- **Синхронный прокси**: Flask держит воркер занятым на всё время ответа FastAPI (до 300 с). При нехватке воркеров — таймауты и 502.
- **Два бэкенда, два кэша**: Flask legacy-маршруты (`/api/admin/*`, `/api/recommendations` и т.д.) и FastAPI `/api/v2/admin/*` дублируют логику; кэш только в FastAPI для v2.

### Путаница
- Дашборд во фронте дергает и v2 (`/api/v2/admin/*`), и legacy (`/api/recommendations`). **Рекомендации** переведены на v2.
- Для «Аналитика» при выборе не «at-risk» фронт ходит в `/api/admin/${view}` (legacy); единого v2-маршрута для всех режимов нет.

### Защита от падений
- В эндпоинтах FastAPI admin (dashboard, community-structure, leaderboard, at-risk-users) добавлен try/except: при ошибке сборки возвращается 500 с `{"ok": false, "error": "..."}` вместо падения процесса.

---

## 3. Данные

| Источник | Кто читает/пишет |
|----------|-------------------|
| **SQLite (bot.db)** | ingest (сообщения, юзеры, рёбра); граф/портрет при включённом DB |
| **user_stats.json** | Бот (история, портрет); админка (дашборды, _collect_messages) |
| **marketing_metrics.json** | Бот (rollup); админка (здоровье, лидерборд, at-risk) |
| **social_graph.json** | Граф (fallback), дайджест |
| **bot_settings.json** | Бот, админка (настройки) |

Дашборды «здоровье», «лидерборд», «at-risk» и `_collect_messages` до сих пор опираются на **JSON** (user_stats, marketing_metrics), а не на БД. Граф может читать из БД при `storage_db_reads_enabled`.

---

## 4. Топ-рекомендации (по приоритету)

1. **Бот**: выносить sync file I/O (`user_stats`, `bot_settings`, `bot_state`) в `asyncio.to_thread` / executor, чтобы не блокировать event loop.
2. **Бот**: кэш `get_me()` — **сделано** (2 мин).
3. **Админка**: один бэкенд для дашборда (FastAPI), постепенно убрать дублирующие Flask `/api/admin/*`, `/api/leaderboard` или проксировать их в v2 с тем же кэшем.
4. **Админка**: при нехватке воркеров — увеличить workers (gunicorn) или вынести вызов FastAPI в отдельный поток/очередь с таймаутом 90–120 с.
5. **Фронт**: рекомендации на v2 — **сделано**; проверка `r.ok` и разбор JSON с запасом на HTML — **сделано**.
6. **Данные**: если нужны дашборды «из БД» — завести чтение здоровья/лидерборда из БД (агрегаты по messages/users) и переключать сборщики по `storage_*`.
7. **Бот**: добавить fallback-хендлер для групповых сообщений без текста/фото/голоса (хотя бы ingest или «не обрабатываю»), чтобы не плодить «not handled».
8. **Мониторинг**: лимиты по памяти (MemoryMax уже есть), таймауты executor, логирование «dropped» (старое сообщение, unhandled content type).

---

## 5. Что уже сделано в этом аудите

- Кэш `bot.get_me()` в `IsDirectedAtBotFilter` (2 мин).
- Try/except в FastAPI admin эндпоинтах → 500 + JSON при ошибке сборки.
- Рекомендации во фронте: запрос на `/api/v2/recommendations`, проверка `r.ok` и безопасный разбор JSON.
- Раннее сокращение числа чатов в _aggregate_health (12) и кэш 45 с в FastAPI для dashboard/community/leaderboard/at-risk.
