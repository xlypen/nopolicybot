# Технический отчет по текущему состоянию проекта

**Дата обновления:** 2026-03-13  
**Проект:** `nopolicybot` / `telegram-political-monitor-bot`  
**Ветка:** `restore-from-archive`  
**Текущий HEAD:** `48bf0ee` (`Add full admin metrics dashboard API layer and UI panels.`)

---

## 1) Executive Summary

Проект находится в рабочем состоянии и в production-режиме обслуживает:

- Telegram-бот (`aiogram`) с модерацией, аналитикой, AI-подсказками и реакциями;
- Flask-админку (legacy + modern UI);
- FastAPI v2 контур (`/api/v2/*`) с health/graph/realtime/monitoring;
- Контур наблюдаемости, hardening и deploy-процедуры.

Стратегические фазы (1-5) завершены; дополнительно выполнен релизный closeout (чеклисты, changelog, pre-release pipeline) и расширен admin metrics layer (8 новых `/api/admin/*` endpoints + UI панели).

---

## 2) Фактическое runtime-состояние (на момент отчета)

Проверено командно:

- `telegram-bot.service` -> `active`
- `telegram-bot-admin.service` -> `active`
- `telegram-bot-api.service` -> `active`
- `GET http://127.0.0.1:5000/health` -> `{"service":"flask-admin","status":"ok"}`
- `GET http://127.0.0.1:8001/api/v2/health` -> `{"status":"ok","version":"2.0.0"}`

Git-состояние:

- локальная ветка синхронизирована с remote (`restore-from-archive...origin/restore-from-archive`)
- рабочее дерево чистое после последнего деплоя

---

## 3) Архитектура и ключевые подсистемы

### 3.1 Application surfaces

- **Bot runtime:** `bot.py`
- **Admin web app (Flask):** `admin_app.py`, `templates/*`, `static/*`
- **API v2 (FastAPI):** `api/main.py`, `api/routers/*`
- **DB and repositories:** `db/*`
- **Analytics and AI services:** `services/*`

### 3.2 Storage

Гибридная модель:

- legacy JSON (`user_stats.json`, `social_graph.json`, runtime state files),
- DB-контур (SQLAlchemy async + репозитории),
- cutover управление и отчеты (`/api/storage/status`, `/api/storage/cutover-report`, `/api/storage/cutover`).

Политика дальнейшего движения: `docs/storage_cutover_policy.md`.

### 3.3 Observability + hardening

- structured logging: `services/structured_logging.py`
- monitoring snapshot/alerts/prometheus export: `services/monitoring.py`
- audit trail: `services/audit_log.py` (`data/audit_events.jsonl`)
- rate limiting: `services/rate_limiter.py`
- URL/body guardrails + auth/audit hardening для Flask/FastAPI.

---

## 4) Выполнение стратегического плана

### 4.1 Фазы 1-5 (статус: выполнено)

Реализованы:

- маркетинговые метрики и decision engine;
- storage unification + migration tooling;
- graph optimization (pipeline, very-large handling, delta updates, realtime UX);
- production hardening (security, observability, CI/CD, staged deploy);
- learning layer (feedback loop, A/B, predictive signals, optimization recommendations).

### 4.2 Пост-фазовые хвосты (статус: выполнено)

- modern dashboard стал default для `/admin`;
- legacy сохраняется на `/admin-legacy` (+ совместимость через `?legacy=1`);
- release readiness package закрыт (changelog/checklists/pre-release workflow/script);
- добавлен полный admin metrics API слой и UI панели.

---

## 5) Admin UI состояние (актуально)

### 5.1 Маршруты интерфейса

- `/admin` -> modern dashboard (default)
- `/admin-legacy` -> legacy admin
- `/settings`, `/social-graph`, `/me`, и вспомогательные страницы

### 5.2 Modern dashboard (`templates/admin/dashboard.html`)

Доступны блоки:

- Graph Canvas + render engine info + live delta
- Retention & At-Risk
- Recommendations + Predictive signals
- Decision audit + feedback action buttons
- Decision quality summary
- Learning summary (A/B + feedback)
- Storage status
- **Новые панели admin metrics:**
  - Chat Health Overview
  - Community Structure
  - User Leaderboard (metric switch)
  - Advanced Analytics Panel (switchable views):
    - At-risk users
    - Decision quality
    - Content analysis
    - Moderation activity
    - Growth & trends

---

## 6) API каталог (актуальный)

### 6.1 Flask `/api/*` (основной admin surface)

- Graph:
  - `/api/chat/<chat_id>/graph`
  - `/api/chat/<chat_id>/graph-delta`
  - `/api/me/graph`
  - `/api/me/graph-delta`
- Core analytics:
  - `/api/chat/<chat_id>/community-health`
  - `/api/chat/<chat_id>/moderation-risk`
  - `/api/metrics/user/<user_id>`
  - `/api/metrics/chat/<chat_id>/health`
  - `/api/leaderboard`
- AI/learning/predictive:
  - `/api/decisions/recent`
  - `/api/decisions/feedback`
  - `/api/decisions/quality`
  - `/api/recommendations`
  - `/api/predictive/overview`
  - `/api/learning/summary`
  - `/api/retention-dashboard`
  - `/api/churn/snapshots`
  - `/api/churn/run`
- Storage/cutover:
  - `/api/storage/status`
  - `/api/storage/cutover-report`
  - `/api/storage/cutover`
- Monitoring:
  - `/api/monitoring/metrics`
  - `/api/monitoring/alerts`

### 6.2 Новый admin metrics API слой (`/api/admin/*`)

Реализованы endpoints:

- `/api/admin/dashboard`
- `/api/admin/community-structure`
- `/api/admin/leaderboard`
- `/api/admin/at-risk-users`
- `/api/admin/decision-quality`
- `/api/admin/content-analysis`
- `/api/admin/moderation-activity`
- `/api/admin/trends`

Агрегация логики вынесена в `services/admin_dashboards.py`.

### 6.3 FastAPI v2 (`/api/v2/*`)

- `/api/v2/health`
- `/api/v2/metrics`
- `/api/v2/alerts`
- routers:
  - `/api/v2/graph/*`
  - `/api/v2/realtime/*`
  - `/api/v2/health/*`

---

## 7) AI / Decision / Learning состояние

Реализованы:

- rule-based + metric-aware strategy selection (`decision_engine`);
- A/B variant assignment и bias composition (`learning_loop`);
- admin feedback ingestion (`/api/decisions/feedback`);
- decision quality aggregation (`/api/decisions/quality`);
- predictive signals:
  - churn risk
  - toxicity
  - virality
- optimization-aware recommendations.

Использование этих сигналов в UI:

- decision quality panel;
- learning summary panel;
- predictive risk cards;
- recommendations feed.

---

## 8) CI/CD и release readiness

### 8.1 Workflows

- `.github/workflows/ci.yml`
  - compile check
  - tests
  - smoke checks
- `.github/workflows/deploy.yml`
  - manual staging/production deployment pipeline
- `.github/workflows/release-readiness.yml`
  - pre-release gate для master PR/manual runs

### 8.2 Скрипты и документы

- `scripts/deploy_release.sh`
- `scripts/smoke_checks.py`
- `scripts/pre_release_check.sh`
- `CHANGELOG.md`
- `docs/release_checklist.md`
- `docs/production_hardening.md`
- `docs/storage_cutover_policy.md`

---

## 9) Тестирование и качество

Проверено на текущем состоянии:

- `pytest -q` -> **92 passed, 6 warnings**
- `python scripts/smoke_checks.py` -> **ok**

Примечания по warning:

- FastAPI `on_event` deprecation (startup/shutdown) — функционально не блокирует, но рекомендуется migration на lifespan handlers.
- legacy `datetime.utcnow()` warning в одном из DB ingest тест-кейсов/зависимостей — не критично для runtime, но желательно дочистить.

---

## 10) Последние ключевые коммиты

1. `48bf0ee` — full admin metrics dashboard API layer + UI panels.
2. `bba2acf` — release readiness package and operational closeout docs.
3. `297f461` — integration coverage for modern/legacy admin routes.
4. `96e2ea4` — refreshed status report.
5. `a94baf2` — `/admin` default modern + `/admin-legacy` fallback.
6. `3b65449` — predictive/learning controls in modern UI.
7. `c6a2c62` — deployment tails + operational templates.

---

## 11) Текущие ограничения и риски

1. **Realtime v2 hardening**  
   Базовая real-time функциональность есть, но для heavy-load сценариев ещё можно усиливать backpressure/persistence модель.

2. **E2E UI automation gap**  
   Нет полноценного пакета e2e для modern dashboard (желательно Playwright/browser automation).

3. **FastAPI lifecycle deprecation tech debt**  
   Требуется переход на lifespan API вместо `@app.on_event`.

4. **Storage finalization**  
   Политика cutover определена, но полный operational rollout до DB-first everywhere требует staged execution.

---

## 12) Рекомендованный следующий этап

1. Провести staging soak 24-48ч по release checklist.
2. Зафиксировать SLO baseline (p95, 5xx ratio, rate-limit hits, alerts).
3. Добавить e2e сценарии для критических путей modern dashboard.
4. После стабильного soak — production promotion по `deploy.yml`.
5. Подготовить и согласовать фактическую миграцию runtime c SQLite на PostgreSQL (инфра, `DATABASE_URL`, миграции схемы, backfill, cutover/rollback).

---

## 13) Краткий вывод

Проект находится в **стабильном рабочем состоянии** с закрытым стратегическим roadmap (фазы 1-5) и доведенным release/process контуром.  
Новый слой `/api/admin/*` и обновленный modern dashboard дают полноценный каталог управленческих метрик прямо в UI.  
Дальше — уже не “восстановление”, а плановое эксплуатационное развитие и hardening под долгую прод-нагрузку.

