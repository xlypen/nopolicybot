# nopolicybot — план действий агента (Stage T)

## Назначение файла

Этот документ является операционным планом для AI-агента, выполняющего задачи по развитию проекта `nopolicybot`. Каждый раздел содержит этапы, конкретные действия, критерии завершения и точки проверки. Агент должен выполнять задачи последовательно внутри каждого этапа, проверяя критерий завершения перед переходом к следующему шагу.

---

## Соглашения

- `[ПРОВЕРИТЬ]` — агент должен прочитать текущее состояние кода/конфига перед действием
- `[ВЫПОЛНИТЬ]` — конкретное действие, которое нужно реализовать
- `[ТЕСТ]` — проверка, что действие выполнено корректно
- `[GATE]` — блокирующая проверка; следующий этап не начинается до прохождения

---

## Блок A — Стабильность и инфраструктура

### Этап A1: SLO baseline

**Цель:** зафиксировать официальные пороги производительности как CI-gate.

- `[ПРОВЕРИТЬ]` Прочитать текущие конфиги: `ci.yml`, `deploy.yml`, `release-readiness.yml`
- `[ПРОВЕРИТЬ]` Найти все места, где упоминаются p95, 5xx, rate-limit в коде и конфигах
- `[ВЫПОЛНИТЬ]` Создать файл `docs/slo.md` с таблицей официальных порогов:
  - p95 latency ≤ 800ms (FastAPI v2)
  - 5xx rate ≤ 1% rolling
  - rate-limit hit ratio ≤ 5%
  - alert volume ≤ N/hour (установить после soak)
- `[ВЫПОЛНИТЬ]` Добавить в `deploy.yml` шаг preflight: автоматическая проверка p95 и 5xx из Prometheus перед production gate
- `[ВЫПОЛНИТЬ]` Добавить в `release-readiness.yml` шаг: читать `docs/slo.md` и сравнивать с текущими метриками
- `[ТЕСТ]` Запустить pipeline на staging — убедиться, что gate срабатывает при нарушении порогов
- `[GATE]` SLO зафиксированы, gate в CI работает, документ опубликован

---

### Этап A2: Gunicorn workers

**Цель:** устранить SPOF admin-сервиса (текущий `-w 1`).

- `[ПРОВЕРИТЬ]` Найти systemd unit-файл для `telegram-bot-admin`, прочитать текущие параметры запуска
- `[ПРОВЕРИТЬ]` Определить количество CPU на production хосте: `nproc`
- `[ВЫПОЛНИТЬ]` Изменить параметры запуска Gunicorn: workers = (2 × CPU) + 1, минимум 3
- `[ВЫПОЛНИТЬ]` Добавить `--timeout 30` и `--keep-alive 5`
- `[ВЫПОЛНИТЬ]` Добавить в systemd unit: `Restart=on-failure`, `RestartSec=5`
- `[ТЕСТ]` Перезапустить сервис, проверить `systemctl status telegram-bot-admin`
- `[ТЕСТ]` Отправить 10 одновременных запросов к `/admin/api/health` — убедиться что все проходят
- `[GATE]` Сервис запускается с N workers, healthcheck проходит под нагрузкой

---

### Этап A3: Coverage gate в CI

**Цель:** сделать покрытие тестами измеримым и блокирующим.

- `[ПРОВЕРИТЬ]` Прочитать `ci.yml`, найти шаг запуска pytest
- `[ПРОВЕРИТЬ]` Запустить `pytest --cov --cov-report=term` локально, зафиксировать текущий %
- `[ВЫПОЛНИТЬ]` Добавить в pytest-команду в CI: `--cov-fail-under=75` (или текущий % − 5 если он выше 75)
- `[ВЫПОЛНИТЬ]` Добавить `--cov-report=xml` для сохранения артефакта
- `[ВЫПОЛНИТЬ]` Добавить шаг в `ci.yml`: сохранять coverage report как GitHub Actions artifact
- `[ТЕСТ]` Временно снизить порог до недостижимого значения — убедиться, что CI падает
- `[ТЕСТ]` Вернуть правильный порог — убедиться, что CI проходит
- `[GATE]` Coverage gate активен, pipeline падает при снижении покрытия

---

### Этап A4: FastAPI lifespan migration

**Цель:** убрать deprecation debt в FastAPI lifecycle.

**Статус:** Выполнено. `api/main.py` использует `@asynccontextmanager` lifespan (init_db, start/stop_realtime_worker). Нет `@app.on_event` в кодовой базе.

- `[ПРОВЕРИТЬ]` Найти все `@app.on_event("startup")` и `@app.on_event("shutdown")` в `api/main.py` и routers
- `[ПРОВЕРИТЬ]` Прочитать текущие startup-задачи: что инициализируется, в каком порядке
- `[ВЫПОЛНИТЬ]` Заменить на `@asynccontextmanager` lifespan pattern:
  ```python
  from contextlib import asynccontextmanager
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      # startup logic
      yield
      # shutdown logic
  app = FastAPI(lifespan=lifespan)
  ```
- `[ВЫПОЛНИТЬ]` Перенести все background tasks внутрь lifespan context
- `[ТЕСТ]` Запустить FastAPI, убедиться что startup выполняется без ошибок
- `[ТЕСТ]` Отправить SIGTERM — убедиться, что shutdown выполняется gracefully
- `[ТЕСТ]` Запустить полный тестовый сьют — 92 passed, 0 новых failures
- `[GATE]` Ни одного `@app.on_event` в кодовой базе, тесты проходят

---

### Этап A5: DB-first cutover

**Цель:** устранить dual storage, сделать PostgreSQL единственным source-of-truth.

- `[ПРОВЕРИТЬ]` Прочитать текущую логику dual storage в `db/` и `services/storage`
- `[ПРОВЕРИТЬ]` Составить полный список всех мест записи в JSON-файлы
- `[ВЫПОЛНИТЬ]` Добавить parity monitor: каждые 5 минут сравнивать содержимое JSON и DB, логировать расхождения в отдельный файл `data/parity_diff.log`
- `[ВЫПОЛНИТЬ]` Добавить feature flag `STORAGE_MODE` в env: `dual` / `db_first` / `db_only`
- `[ВЫПОЛНИТЬ]` При `db_first`: все записи идут в DB, JSON читается только как fallback
- `[ТЕСТ]` Включить `STORAGE_MODE=db_first` на staging, проверить parity_diff.log в течение 7 дней
- `[GATE A]` 7 дней без критических расхождений в parity log → переходить к db_only
- `[ВЫПОЛНИТЬ]` Переключить на `STORAGE_MODE=db_only`
- `[ТЕСТ]` Мониторить ошибки 14 дней, проверять что JSON не используется
- `[GATE B]` 14 дней без критических ошибок → удалить JSON write path из кода

---

### Этап A6: Realtime WebSocket hardening

**Цель:** устранить риск queue saturation и добавить горизонтальное масштабирование.

**Статус:** Выполнено. BroadcastManager: slow_warn_threshold=0.8, graceful disconnect 1008 при overflow, ws_queue_utilization в Prometheus, Redis Pub/Sub при REDIS_URL.

- `[ПРОВЕРИТЬ]` Прочитать `api/` — найти broadcast manager, текущий queue_size=64 и heartbeat логику
- `[ВЫПОЛНИТЬ]` Добавить slow client detection: если очередь клиента заполнена > 80% — отправить warning frame
- `[ВЫПОЛНИТЬ]` Добавить graceful disconnect при overflow: закрыть соединение с кодом 1008 (policy violation), залогировать
- `[ВЫПОЛНИТЬ]` Добавить метрику: `ws_queue_utilization` по каждому chat_id → экспортировать в Prometheus
- `[ВЫПОЛНИТЬ]` Реализовать Redis Pub/Sub backend для broadcast manager:
  - При наличии `REDIS_URL` — использовать Redis как message bus
  - Без Redis — текущий in-process fallback
- `[ТЕСТ]` Симулировать 50 одновременных WS-соединений на одном chat_id
- `[ТЕСТ]` Симулировать slow client — убедиться, что остальные клиенты не деградируют
- `[GATE]` Queue utilization метрика присутствует в Prometheus, overflow обрабатывается gracefully

---

## Блок B — Безопасность

### Этап B1: Убрать дефолтные секреты

**Цель:** fail-fast при неправильном конфиге вместо silent security failure.

- `[ПРОВЕРИТЬ]` Найти все места с `change-me`, `default`, `secret` в конфигах и env-defaults
- `[ПРОВЕРИТЬ]` Составить список всех критических env-переменных (API токены, admin secret, DB password, bot token)
- `[ВЫПОЛНИТЬ]` Создать `config/validate_secrets.py`:
  - При старте проверять каждую критическую переменную
  - Если значение пустое, None, или равно любому из known-дефолтов — `sys.exit(1)` с явным сообщением
- `[ВЫПОЛНИТЬ]` Вызвать `validate_secrets()` в startup для всех трёх сервисов (bot, admin, api)
- `[ВЫПОЛНИТЬ]` Добавить в `.env.example` все переменные с комментариями (без реальных значений)
- `[ТЕСТ]` Запустить сервис с дефолтным значением секрета — убедиться, что падает с читаемой ошибкой
- `[ТЕСТ]` Запустить с правильными значениями — убедиться, что запускается нормально
- `[GATE]` Ни один сервис не запускается с дефолтными секретами

---

### Этап B2: CORS allowlist

**Цель:** заменить `allow_origins=["*"]` на явный список.

- `[ПРОВЕРИТЬ]` Найти CORS-конфиг в `api/main.py` и Flask admin
- `[ПРОВЕРИТЬ]` Определить реальные origin: production admin domain, staging domain
- `[ВЫПОЛНИТЬ]` Добавить env-переменную `ALLOWED_ORIGINS` (comma-separated список)
- `[ВЫПОЛНИТЬ]` В FastAPI: `allow_origins=settings.ALLOWED_ORIGINS.split(",")`
- `[ВЫПОЛНИТЬ]` В Flask: аналогичный механизм через flask-cors
- `[ВЫПОЛНИТЬ]` В `.env.example`: `ALLOWED_ORIGINS=https://admin.yourdomain.com`
- `[ТЕСТ]` Отправить запрос с Origin: `https://evil.example.com` — убедиться, что получает 403
- `[ТЕСТ]` Отправить запрос с правильным Origin — убедиться, что проходит
- `[GATE]` CORS заблокирован для неизвестных origin, настраивается через env

---

### Этап B3: Systemd non-root

**Цель:** убрать избыточные привилегии сервисов.

- `[ПРОВЕРИТЬ]` Прочитать все три systemd unit-файла, зафиксировать текущего User
- `[ВЫПОЛНИТЬ]` Создать dedicated system user: `useradd -r -s /bin/false -d /opt/nopolicybot nopolicybot`
- `[ВЫПОЛНИТЬ]` Изменить ownership файлов проекта: `chown -R nopolicybot:nopolicybot /opt/nopolicybot`
- `[ВЫПОЛНИТЬ]` Обновить все три unit-файла:
  ```ini
  User=nopolicybot
  Group=nopolicybot
  NoNewPrivileges=true
  PrivateTmp=true
  ReadWritePaths=/opt/nopolicybot/data
  ```
- `[ТЕСТ]` `systemctl restart` все три сервиса, проверить `ps aux | grep python` — не root
- `[ТЕСТ]` Все три healthcheck endpoint отвечают 200
- `[GATE]` Ни один сервис не запускается от root

---

### Этап B4: GDPR / PII политика

**Цель:** задокументировать и реализовать контроль над персональными данными.

- `[ПРОВЕРИТЬ]` Аудит всех таблиц DB и JSON-файлов: что хранится, какие поля содержат PII
- `[ВЫПОЛНИТЬ]` Создать `docs/data_map.md`: таблица со столбцами `тип данных | где хранится | retention | PII?`
- `[ВЫПОЛНИТЬ]` Реализовать auto-delete: background задача, удаляет raw сообщения старше N дней (N через env `MESSAGE_RETENTION_DAYS`, дефолт 90)
- `[ВЫПОЛНИТЬ]` Добавить API endpoint `DELETE /api/v2/users/{user_id}/data` — удаление всех данных пользователя
- `[ВЫПОЛНИТЬ]` В аналитических запросах: использовать `user_hash` вместо `user_id` там, где полный ID не нужен
- `[ТЕСТ]` Вызвать endpoint удаления, проверить что все записи пользователя удалены из DB и JSON
- `[ТЕСТ]` Запустить auto-delete job, проверить что старые записи удалены
- `[GATE]` Data map задокументирован, right-to-erasure endpoint работает, retention job активен

---

## Блок C — UI / UX

### Этап C1: Health dashboard — единый экран

**Цель:** единая точка входа для оперативного мониторинга состояния системы.

- `[ПРОВЕРИТЬ]` Изучить текущий modern dashboard (`/admin`): какие виджеты уже есть
- `[ПРОВЕРИТЬ]` Изучить все `/admin/api/*` endpoints — какие данные доступны
- `[ВЫПОЛНИТЬ]` Создать маршрут `/admin/health-overview` с компонентами:
  - Верхняя строка: system status (bot / admin / api), uptime, active chats count
  - Центральная область: топ-10 at-risk пользователей с score, топ-5 активных конфликтов
  - Правая колонка: последние 20 AI-решений с типом (reply/warn/DM) и confidence
- `[ВЫПОЛНИТЬ]` Подключить к существующему WS (`/api/v2/realtime/ws`) для live-обновления
- `[ТЕСТ]` Открыть страницу, убедиться что данные загружаются без ручного обновления
- `[ТЕСТ]` Создать тестовый at-risk сигнал — убедиться, что он появляется на дашборде в течение heartbeat interval
- `[GATE]` Страница доступна, данные обновляются в реальном времени через WS

---

### Этап C2: Панель решений модератора

**Цель:** UI для review, override и обратной связи на AI-решения — основа learning loop.

- `[ПРОВЕРИТЬ]` Изучить структуру audit_events.jsonl и decision engine output — какие поля доступны
- `[ВЫПОЛНИТЬ]` Создать маршрут `/admin/decisions` с карточной очередью:
  - Каждая карточка: исходное сообщение + автор + AI-решение + confidence score
  - Кнопки: `Approve` / `Override` / `Escalate`
  - Фильтры: тип действия, чат, confidence range
- `[ВЫПОЛНИТЬ]` При нажатии `Override` — показать форму: выбрать правильный тип (`false_alarm` / `wrong_severity` / `missed`) + опциональный комментарий
- `[ВЫПОЛНИТЬ]` Все override записываются в DB таблицу `decision_feedback` с полями: `decision_id`, `original_action`, `override_type`, `operator_id`, `timestamp`, `comment`
- `[ВЫПОЛНИТЬ]` Добавить сводную статистику вверху страницы: approve rate / override rate за последние 7 дней
- `[ТЕСТ]` Пройти полный цикл: AI-решение → override → проверить запись в DB
- `[GATE]` Очередь работает, override сохраняется в structured формате пригодном для обучения

---

### Этап C3: Консолидация legacy + modern UI

**Цель:** один UI-контур вместо двух.

- `[ПРОВЕРИТЬ]` Составить список всех маршрутов `/admin-legacy/*`, которых нет в `/admin/*`
- `[ПРОВЕРИТЬ]` Опросить пользователей (или проанализировать логи): какие legacy-страницы используются активно
- `[ВЫПОЛНИТЬ]` Перенести gap-функциональность в modern dashboard (по приоритету использования)
- `[ВЫПОЛНИТЬ]` Добавить на все legacy-маршруты deprecation banner: "Эта страница будет удалена. Используйте /admin/..."
- `[ВЫПОЛНИТЬ]` Через 30 дней: сделать `/admin-legacy` redirect на соответствующий modern маршрут
- `[ТЕСТ]` Все функции legacy доступны в modern dashboard
- `[ТЕСТ]` Все legacy URL возвращают redirect (301) на modern эквивалент
- `[GATE]` Legacy URL недоступны напрямую, все функции перенесены

---

### Этап C4: Интерактивный граф

**Цель:** социальный граф как основной инструмент работы модератора.

- `[ПРОВЕРИТЬ]` Изучить текущий граф-рендеринг в dashboard — какая библиотека, какие данные
- `[ПРОВЕРИТЬ]` Изучить `/api/v2/graph` endpoints — какие данные доступны (nodes, edges, influence score)
- `[ВЫПОЛНИТЬ]` Интегрировать vis-network или Cytoscape.js для интерактивного рендеринга
- `[ВЫПОЛНИТЬ]` Clickable nodes: клик на узел → боковая панель с карточкой пользователя:
  - influence score, centrality, warn history, active time
- `[ВЫПОЛНИТЬ]` Фильтры: по influence score (slider), по at-risk флагу, по активности за период
- `[ВЫПОЛНИТЬ]` Highlight shortest path между двумя выбранными пользователями (через networkx API endpoint)
- `[ВЫПОЛНИТЬ]` Timeline-слайдер: граф на момент времени T (если история дельт хранится)
- `[ТЕСТ]` Загрузить граф с 1000+ узлов — проверить производительность рендеринга (< 3 сек)
- `[ТЕСТ]` Кликнуть на узел — убедиться что карточка открывается с правильными данными
- `[GATE]` Граф интерактивен, карточка пользователя работает, фильтры применяются

---

## Блок D — AI / ML

### Этап D1: Quality scorecard

**Цель:** сделать качество AI-решений измеримым.

- `[ПРОВЕРИТЬ]` Изучить структуру decision engine output и audit trail
- `[ВЫПОЛНИТЬ]` Создать таблицу `decision_quality_log` в DB:
  - `decision_id`, `action_type`, `confidence`, `was_overridden`, `override_type`, `chat_id`, `timestamp`
- `[ВЫПОЛНИТЬ]` После каждого AI-решения — записывать в таблицу
- `[ВЫПОЛНИТЬ]` После каждого override (из Этапа C2) — обновлять соответствующую запись
- `[ВЫПОЛНИТЬ]` Создать API endpoint `GET /api/v2/metrics/ai-quality` возвращающий:
  - `approval_rate`, `override_rate`, `fpr_estimate` (override_type=false_alarm / total), `fnr_estimate` (требует ручного аудита)
  - Разбивку по `action_type` и по `chat_id`
- `[ВЫПОЛНИТЬ]` Добавить scorecard виджет на health dashboard (Этап C1)
- `[ТЕСТ]` Сгенерировать тестовые решения + overrides, проверить что метрики считаются корректно
- `[GATE]` Quality metrics доступны через API, отображаются на дашборде

---

### Этап D2: Structured labels при override

**Цель:** превратить каждый override в structured training example.

- `[ПРОВЕРИТЬ]` Изучить данные, которые записываются в decision_feedback (Этап C2)
- `[ВЫПОЛНИТЬ]` Расширить схему `decision_feedback`:
  - Добавить поле `correct_action` (что должен был сделать бот: `none` / `reply` / `warn` / `dm`)
  - Добавить поле `context_notes` (свободный текст, опционально)
- `[ВЫПОЛНИТЬ]` Обновить UI override-формы (Этап C2) — добавить выбор `correct_action`
- `[ВЫПОЛНИТЬ]` Создать export script `scripts/export_training_data.py`:
  - Выгружает все decision_feedback с original message context в JSONL формат
  - Формат совместим с OpenAI fine-tuning format
- `[ТЕСТ]` Создать 10 тестовых override, запустить export — убедиться что JSONL валиден
- `[GATE]` Training data экспортируется в структурированном формате, 200+ labeled examples накоплено (ожидаемо)

---

### Этап D3: Расширенный контекст для AI

**Цель:** улучшить качество анализа через передачу большего контекста модели.

- `[ПРОВЕРИТЬ]` Изучить текущий промпт / вызов API в decision engine — что передаётся сейчас
- `[ВЫПОЛНИТЬ]` Добавить в контекст запроса к AI:
  - Последние N сообщений треда (N=10, настраивается через env `AI_CONTEXT_MESSAGES`)
  - Topic policy текущего чата (из topic_policies)
  - Краткая история пользователя: количество warn, дата последнего флага, influence score
- `[ВЫПОЛНИТЬ]` Добавить эксперимент: логировать отдельно решения с N=0, N=5, N=10 контекстом
- `[ТЕСТ]` Прогнать 50 исторических сообщений через обе версии (без контекста и с контекстом), сравнить override rate
- `[GATE]` Расширенный контекст включён, override rate не вырос (или снизился)

---

### Этап D4: Confidence-based routing

**Цель:** использовать confidence score для управления автономностью бота.

- `[ПРОВЕРИТЬ]` Найти в decision engine где возвращается confidence, как он используется сейчас
- `[ВЫПОЛНИТЬ]` Добавить env-переменные для порогов:
  - `AI_CONFIDENCE_AUTO` — выше этого: полностью автономное действие (дефолт: 0.85)
  - `AI_CONFIDENCE_NOTIFY` — между notify и auto: действие + уведомление модератору (дефолт: 0.60)
  - Ниже `AI_CONFIDENCE_NOTIFY`: отправить в очередь на ручной review без автоматического действия
- `[ВЫПОЛНИТЬ]` Реализовать routing в decision engine согласно порогам
- `[ВЫПОЛНИТЬ]` В UI очереди решений (Этап C2): отображать confidence badge с цветовой кодировкой
- `[ТЕСТ]` Симулировать решение с confidence 0.4 — убедиться что попадает в очередь, не выполняется автоматически
- `[ТЕСТ]` Симулировать решение с confidence 0.9 — убедиться что выполняется автоматически
- `[GATE]` Три уровня routing работают, настраиваются через env, отображаются в UI

---

### Этап D5: Active learning loop

**Цель:** замкнуть цикл: override → обучение → улучшение модели.

- `[ПРОВЕРИТЬ]` Убедиться что Этапы D1, D2, D3 завершены и данные накоплены
- `[ПРОВЕРИТЬ]` Проверить количество labeled examples в `decision_feedback`: нужно минимум 200
- `[ВЫПОЛНИТЬ]` Реализовать few-shot prompt update pipeline:
  - Отобрать топ-50 override примеров (наиболее уверенные ручные коррекции)
  - Включить их в system prompt как few-shot examples
  - Версионировать промпты: `prompts/v{N}/decision_prompt.txt`
- `[ВЫПОЛНИТЬ]` Настроить A/B тест: 10% трафика на новый промпт, 90% на текущий
- `[ВЫПОЛНИТЬ]` Сравнить override_rate между версиями за 7 дней
- `[ТЕСТ]` Убедиться что A/B split работает корректно (проверить по логам)
- `[GATE]` Новый промпт показывает override_rate ≤ текущего → выкатить на 100%

---

### Этап D6: Объяснимость at-risk предсказаний

**Цель:** модераторы понимают почему пользователь помечен как at-risk.

- `[ПРОВЕРИТЬ]` Изучить predictive model в `services/` — какие features используются
- `[ВЫПОЛНИТЬ]` Добавить SHAP или rule-based explanation к каждому at-risk предсказанию:
  - Если ML-модель: интегрировать `shap` библиотеку, сохранять top-3 feature importances
  - Если rule-based: возвращать список сработавших правил с весами
- `[ВЫПОЛНИТЬ]` Добавить в API endpoint at-risk пользователей поле `explanation`:
  ```json
  {
    "user_id": "...",
    "risk_score": 0.78,
    "explanation": [
      {"factor": "activity_drop", "weight": 0.45, "description": "Активность снизилась на 60% за 7 дней"},
      {"factor": "conflict_rate", "weight": 0.33, "description": "Рост конфликтных сообщений"}
    ]
  }
  ```
- `[ВЫПОЛНИТЬ]` Отобразить explanation в карточке пользователя на интерактивном графе (Этап C4)
- `[ТЕСТ]` Запросить at-risk список — убедиться что explanation присутствует для каждого пользователя
- `[GATE]` Explanation доступен через API и отображается в UI

---

## Контрольные точки

После завершения каждого блока — запустить полный тестовый сьют:

```bash
pytest -q
python scripts/smoke_checks.py
```

Ожидаемый результат: не меньше исходных 92 passed, 0 новых failures.

После каждого деплоя на staging — проверить:
1. Все три systemd-сервиса активны (`systemctl status telegram-bot telegram-bot-admin telegram-bot-api`)
2. Healthcheck endpoints отвечают 200: `/api/v2/health`, `/admin/api/health`
3. Нет критических записей в `data/audit_events.jsonl` за последние 10 минут

---

## Зависимости между этапами

```text
A1 (SLO) ────────────────────────────────── обязателен перед любым деплоем
A2 (Gunicorn) ──── независим
A3 (Coverage) ─────────────────────────────── независим
B1 (Secrets) ──── независим, выполнить первым в блоке B
B2 (CORS) ─────── независим
B3 (Systemd) ──── независим
C2 (Decisions UI) ──── требует C1 (Health dashboard)
D2 (Labels) ──────── требует C2 (Decisions UI)
D5 (Learning loop) ── требует D1 + D2 + D3, минимум 200 labeled examples
D6 (Explainability) ── требует C4 (Graph UI)
A5 (DB cutover) ────── выполнять последним в блоке A, независимо от C и D
```
