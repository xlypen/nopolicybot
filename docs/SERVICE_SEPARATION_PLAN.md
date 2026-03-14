# План разделения на отдельные сервисы

Дата: 2026-03-14  
Цель: довести до конца разделение, частично реализованное через FastAPI v2.

---

## 1. Текущее состояние

### Уже выделенные сервисы

| Сервис | Процесс | Порт | Маршруты |
|--------|---------|------|----------|
| **Bot** | `bot.py` (telegram-bot.service) | — | Telegram long-polling |
| **Admin UI** | `admin_app.py` (telegram-bot-admin.service) | 5000 | HTML, login, сессии, **все** `/api/*` |
| **API v2** | `run_api.py` (telegram-bot-api.service) | 8001 | `/api/v2/*` |

### Что уже в FastAPI v2

- `GET /api/v2/health` — healthcheck
- `GET /api/v2/metrics` — Prometheus
- `GET /api/v2/alerts` — алерты
- `GET /api/v2/graph/{chat_id}` — граф (с delta)
- `GET /api/v2/realtime/ws/{chat_id}` — WebSocket realtime
- `DELETE /api/v2/users/{user_id}/data` — удаление данных пользователя

### Что остаётся в Flask (admin_app)

**API, используемые админкой и участниками:**

- `/api/chat/<chat>/graph`, `graph-delta`, `graph-lab`, `conflict-prediction`
- `/api/chat/<chat>/community-health`, `moderation-risk`, `digest-preview`, `analysis`
- `/api/admin/*` — dashboard, community-structure, leaderboard, at-risk-users, at-risk-action, decision-quality, content-analysis, moderation-activity, trends
- `/api/recommendations`, `recommendations/mark-done`
- `/api/predictive/overview`
- `/api/storage/status`, `cutover-report`, `cutover`
- `/api/portrait-*`, `/api/user/<id>/portrait-*`, `/api/portrait-classify-unknown`
- `/api/decisions/recent`, `feedback`, `quality`
- `/api/metrics/user/<id>`, `/api/metrics/chat/<id>/health`
- `/api/leaderboard`
- `/api/retention-dashboard`, `/api/learning/summary`
- `/api/churn/snapshots`, `/api/churn/run`
- `/api/me/graph`, `graph-delta`, `graph-version` (participant token)
- `/api/settings`, `/api/chat-mode`, `/api/reset-political-count`
- `/api/log-tail`, `/api/prompts`, `/api/topic-policies`
- `/restart-bot`, `/api/restart-status`
- и др.

**UI-маршруты:** `/`, `/login`, `/admin`, `/admin-modern`, `/admin-legacy`, `/me`, `/settings`, `/user/<id>`, `/chat/<id>/analysis`, и т.д.

### Nginx

- `/api/v2/*` → FastAPI (8001)
- Всё остальное → Flask (5000)

---

## 2. Целевая архитектура

### Вариант A: API-сервис (рекомендуемый)

**Идея:** Все данные и бизнес-логика — в FastAPI. Flask — только UI и сессии.

| Сервис | Роль |
|--------|------|
| **API** (FastAPI) | Все `/api/*` endpoints, auth (Bearer + session cookie), БД, сервисы |
| **Admin** (Flask) | HTML-страницы, login/logout, сессии, проксирование API-запросов или прямой вызов FastAPI |
| **Bot** | Telegram, вызов API для чтения/записи |

### Вариант B: Микросервисы

| Сервис | Роль |
|--------|------|
| **Graph API** | Граф, community, graph-lab, delta |
| **Admin API** | Dashboard, recommendations, predictive, at-risk, decisions, metrics |
| **Realtime** | WebSocket, broadcast |
| **Admin UI** | Flask, только HTML + сессии |
| **Bot** | Telegram |

### Вариант C: Минимальный (быстрый)

**Идея:** Перенести в FastAPI только дублирующие и тяжёлые endpoints. Flask остаётся основным.

- Graph API уже в v2 — **переключить админку** на `/api/v2/graph/` вместо `/api/chat/.../graph`
- Realtime уже в v2 — админка уже может использовать WS
- Остальное оставить в Flask до следующей фазы

---

## 3. План по фазам (Вариант A)

### Фаза 1: Унификация Graph API (1–2 дня)

**Цель:** Админка и participant-me используют один источник — FastAPI v2.

1. Добавить в FastAPI v2:
   - `GET /api/v2/graph/{chat_id}/lab` — graph-lab с фильтрами (или расширить query params)
   - Поддержка `chat_id=all` (или `0`) для агрегации
   - Поддержка `ego_user`, `period`, `limit` в query
2. Обновить `templates/admin/dashboard.html`: заменить `/api/chat/.../graph` и `graph-delta` на `/api/v2/graph/...`
3. Обновить `templates/admin/user_profile.html` и `participant_me.html` аналогично
4. Добавить в FastAPI auth по session cookie (если админка логинится через Flask) или проксировать через Flask с подстановкой Bearer

**Результат:** Graph-запросы идут в FastAPI, дублирование graph-логики в Flask можно удалить.

### Фаза 2: Auth и прокси (2–3 дня)

**Цель:** Единая схема аутентификации для API.

1. Ввести **shared session**: Flask и FastAPI читают одну сессию (Redis или signed cookie с общим секретом)
2. Или: Flask при login выдаёт JWT/Bearer, админка хранит в localStorage и шлёт в заголовке при вызовах API
3. Добавить в FastAPI middleware проверки session/JWT для `/api/v2/*` (кроме health, docs)
4. Обновить CORS и ALLOWED_ORIGINS для same-origin запросов с админки

**Результат:** Админка может вызывать FastAPI напрямую с учётом сессии.

### Фаза 3: Миграция Admin API (3–5 дней)

**Цель:** Перенести в FastAPI endpoints, используемые админкой.

**Приоритетные группы:**

1. **Dashboard & Analytics**
   - `/api/admin/dashboard` → `GET /api/v2/admin/dashboard`
   - `/api/admin/community-structure` → `GET /api/v2/admin/community-structure`
   - `/api/admin/leaderboard` → `GET /api/v2/admin/leaderboard`
   - `/api/admin/at-risk-users` → `GET /api/v2/admin/at-risk-users`
   - `POST /api/admin/at-risk-action` → `POST /api/v2/admin/at-risk-action`

2. **Recommendations & Predictive**
   - `/api/recommendations` → `GET /api/v2/recommendations`
   - `POST /api/recommendations/mark-done` → `POST /api/v2/recommendations/mark-done`
   - `/api/predictive/overview` → `GET /api/v2/predictive/overview`

3. **Storage & Chat**
   - `/api/storage/status`, `cutover-report`, `cutover` → `/api/v2/storage/*`
   - `/api/chat/<id>/community-health`, `moderation-risk`, `digest-preview`, `analysis` → `/api/v2/chat/<id>/*`

4. **Portrait & User**
   - `/api/portrait-*`, `/api/user/<id>/portrait-*` → `/api/v2/portrait/*`, `/api/v2/user/<id>/portrait/*`
   - `/api/metrics/user/<id>`, `/api/metrics/chat/<id>/health` → `/api/v2/metrics/*`

5. **Settings & Ops**
   - `/api/settings`, `/api/chat-mode`, `/api/reset-political-count` → `/api/v2/settings/*`, `/api/v2/ops/*`
   - `/api/log-tail`, `/api/prompts`, `/api/topic-policies` → `/api/v2/admin/*`

**Для каждой группы:**
- Создать router в `api/routers/`
- Перенести логику из `admin_app.py` в сервисы или прямо в handlers
- Обновить вызовы в `templates/`
- Удалить или пометить deprecated в Flask

### Фаза 4: Упрощение Flask (1–2 дня)

**Цель:** Flask — только UI.

1. Удалить из `admin_app.py` все перенесённые API-маршруты
2. Оставить: `/`, `/login`, `/logout`, `/admin`, `/admin-modern`, `/admin-legacy`, `/me`, `/settings`, `/user/<id>`, рендер HTML, статика
3. При необходимости: тонкий прокси для legacy-клиентов (если есть внешние интеграции)

### Фаза 5: Разделение Bot (опционально)

**Цель:** Бот вызывает API вместо прямого доступа к БД/сервисам.

1. Выделить в API endpoints для бота: отправка сообщений, получение настроек чата, запись решений
2. Бот переходит на HTTP-вызовы к API вместо импорта `services.*`
3. Упрощает тестирование и позволяет масштабировать бота отдельно

---

## 4. Технические решения

### Общие зависимости

- **БД:** общая для всех сервисов (SQLite/PostgreSQL)
- **Секреты:** общий `.env`, `validate_secrets` при старте
- **Логирование:** единый формат, `configure_logging`

### Auth

- **Admin:** session cookie (Flask) → при вызове API v2 передавать cookie или JWT
- **API v2:** `Authorization: Bearer <ADMIN_TOKEN>` или проверка session cookie
- **Participant:** `?token=...` для `/api/me/*`

### Nginx

Текущая схема сохраняется: `/api/v2/*` → 8001, остальное → 5000. После миграции можно ввести `/api/` → 8001, а Flask оставить только для HTML-путей.

### Мониторинг

- Prometheus: `GET /api/v2/metrics` уже есть
- Health: `GET /api/v2/health`
- Алерты: `GET /api/v2/alerts`

---

## 5. Критерии готовности

- [ ] Админка не вызывает Flask для graph, recommendations, admin dashboard, storage, portrait
- [ ] Все данные идут через FastAPI v2
- [ ] Flask содержит только UI-маршруты и сессии
- [ ] Тесты проходят для обоих сервисов
- [ ] Документация API актуальна (`/api/v2/docs`)

---

## 6. Риски и митигации

| Риск | Митигация |
|------|-----------|
| Регрессия при миграции | Поэтапный перенос, smoke-тесты после каждой группы |
| Разные форматы ответов | Использовать те же сервисы (`admin_dashboards`, `graph_api` и т.д.) |
| Сессия не работает между Flask и FastAPI | Shared Redis или JWT после login |
| Долгий рефакторинг | Начать с Фазы 1 (graph) — быстрый выигрыш |

---

## 7. Ссылки

- `api/main.py` — точка входа FastAPI v2
- `admin_app.py` — текущие Flask-маршруты
- `deploy-ubuntu/nginx-nopolicybot.conf` — маршрутизация Nginx
- `STRATEGIC_DEVELOPMENT_PLAN.md` — общий стратегический план
