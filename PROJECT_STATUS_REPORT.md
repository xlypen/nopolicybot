# Технический отчет по текущему состоянию проекта

Дата: 2026-03-13  
Проект: `nopolicybot` / `telegram-political-monitor-bot`  
Ветка: `restore-from-archive`  
Последний commit: `f1264ff` (`Advance recovery plan with runtime health and modern UI endpoints.`)

---

## 1) Что это за бот и проект

`nopolicybot` — это Telegram-бот с админ-панелью для модерации и аналитики чатов:

- детектирует политический/конфликтный контент в сообщениях;
- определяет тональность и применяет сценарии реакции (эмодзи, замечания, поощрения);
- ведет пользовательские профили, историю и контекст;
- строит социальный граф взаимодействий (кто с кем общается, вес и направление связей);
- предоставляет веб-админку для управления настройками и просмотра аналитики;
- имеет FastAPI v2-контур для API/графа/реалтайма.

Проект гибридный: есть legacy JSON-хранилище (рабочий основной контур бота) и новый FastAPI/DB-контур (восстановлен и запущен, но не полностью доминирующий в проде).

---

## 2) Архитектура (актуально)

- **Бот:** `aiogram` (`bot.py`) + AI-слой (`ai_analyzer.py`) + настройки (`bot_settings.py`).
- **Хранилище (legacy):** `user_stats.json`, `social_graph.json`, `bot_settings.json`.
- **Админка:** Flask (`admin_app.py`) + Jinja templates (`templates/*`) + статика (`static/*`).
- **API v2:** FastAPI (`api/main.py`, `api/routers/*`, `run_api.py`).
- **DB-контур:** SQLAlchemy async (`db/*`, репозитории, схемы, миграционный скрипт).
- **Реалтайм:** WebSocket heartbeat в `api/routers/realtime.py` (базовый рабочий контур).

---

## 3) Возможности системы

### 3.1 Возможности бота

- модерация политических тем (пороги, стили, счётчики, reset-логика);
- анализ изображений и voice (при включенных флагах);
- фактчек с ограничениями по длине и throttle;
- адресные/контекстные ответы (rude/kind/technical/substantive);
- режимы паузы диалога и снятия паузы;
- персональные портреты и question-of-day;
- сбор данных для social graph;
- объяснимость действий (если включена в настройках).

### 3.2 Возможности админ-панели

- страницы: `/`, `/login`, `/admin`, `/admin-modern`, `/settings`, `/social-graph`, `/user/<id>`, `/chat/<id>/analysis`, `/me`;
- управление настройками бота (`/settings`, `/api/settings`);
- управление режимами чата (`/api/chat-mode`);
- операционные endpoint’ы (перезапуск/статус, сбросы, портреты, question-of-day, explainability, digest/analysis);
- логи сервисов в UI: `/api/log-tail`;
- управление промптами: `/api/prompts`.

### 3.3 Возможности графа и аналитики

- API графа: `/api/chat/<chat_id>/graph`, `/api/me/graph`;
- аналитика: `/api/chat/<chat_id>/community-health`, `/api/chat/<chat_id>/moderation-risk`;
- UI-режимы визуализации:
  - базовые: `force`, `radial`, `community`, `matrix` (`static/js/graph_visualizations.jsx`);
  - расширенные: `sankey`, `hierarchy`, `bubble`, `ego` (`static/js/advanced_graph_viz.jsx`);
- современный dashboard: `/admin-modern` (восстановлен как отдельный UI-контур).

---

## 4) Что сделано на текущем этапе (факт)

### 4.1 Восстановление и запуск

- выполнено массовое восстановление файлов из архивного снапшота;
- восстановлены ключевые модули бота, админки, шаблонов, сервисов и утилит;
- восстановлен `bot_settings.py` с `DEFAULTS`, пресетами режимов чата и override-механикой;
- добавлены совместимые API-маршруты для контрактов тестов и UI;
- добавлен `GET /health` в Flask-контур;
- поднят FastAPI service unit `telegram-bot-api.service`.

### 4.2 Промпты

- восстановлен централизованный реестр промптов (`data/bot_prompts.json`);
- реализованы функции `get_prompt/get_all_prompts/set_prompt/reset_prompts`;
- восстановлен API для управления промптами из админки.

### 4.3 UI по recovery-плану

- добавлен modern admin shell:
  - `templates/admin/base.html`
  - `templates/admin/dashboard.html`
  - маршрут `/admin-modern`
- добавлены recovery-артефакты:
  - `templates_admin_base.html`
  - `templates_admin_dashboard.html`
- подключен MIME для `.jsx` в Flask.

### 4.4 Текущее операционное состояние

- `telegram-bot.service`: **active**
- `telegram-bot-admin.service`: **active**
- `telegram-bot-api.service`: **active**
- `GET http://127.0.0.1:5000/health`: **200**
- `GET http://127.0.0.1:8001/api/v2/health`: **200**
- автотесты: **12 passed** (локально)

---

## 5) Что НЕ сделано / ограничения

1. **План из outputs закрыт частично по глубине UI-графов**  
   Базовые и расширенные JS-модули восстановлены, но это не полный production D3-пайплайн (часть режимов сейчас реализована в упрощенном виде, без всех прежних UX-деталей и оптимизаций very-large graphs).

2. **Двойной контур (legacy + new) пока не полностью унифицирован**  
   Бот и значимая часть аналитики по-прежнему опираются на JSON-контур; DB/FastAPI контур живой, но не полностью основной.

3. **Недозакрыт production-hardening стек**  
   Nginx/logrotate/reverse-proxy политики и полный operational playbook не финализированы в текущем срезе.

4. **FastAPI realtime — базовый**  
   WebSocket содержит heartbeat-контур; полноценный BroadcastManager/backpressure еще не реализован.

5. **Техдолг по консолидации UI**  
   Есть legacy admin и modern admin одновременно; требуется финальная унификация шаблонов/стилей/роутов.

---

## 6) Что рекомендуется сделать следующим шагом

1. Свести legacy и modern UI в один стабильный контур и зафиксировать контракт API.
2. Доработать граф-модули до полного паритета с целевым UX (зум, легенды, мосты, производительность на больших графах).
3. Определить основной storage-контур (JSON vs DB) и завершить миграционный cutover.
4. Реализовать полноценный realtime broadcast layer (WebSocket manager).
5. Подготовить PR в `master` с changelog и чек-листом smoke/e2e.

---

## 7) Краткий вывод

Проект **в рабочем состоянии**: бот, админка и FastAPI сервисы подняты, ключевые endpoint’ы отвечают, тесты проходят.  
Восстановление по архиву и recovery-плану выполнено, включая запуск и базовый modern UI.  
Основной оставшийся объем — **стабилизация и доведение до production-ready уровня** (унификация контуров, полнота граф-UX, hardening и operational polish).

