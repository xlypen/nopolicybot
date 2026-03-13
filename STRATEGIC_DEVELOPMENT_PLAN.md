# Стратегический план развития nopolicybot

Дата: 2026-03-13  
Статус: Post-Recovery Development Phase  
Горизонт: Production-Grade, Auto-Managed

---

## 1. Цели и приоритеты

### Сильные стороны (текущая база)
- Модульная архитектура (бот + админка + API).
- Два контура (legacy JSON + modern DB/FastAPI) работают.
- Бот и модерация в проде функционируют.
- Есть social graph и базовая визуализация.
- Есть админ-панель управления.
- API v2 поднят, healthcheck отвечает.
- Централизованная prompt-система.
- Базовый WebSocket realtime.

### Критичные проблемы
- Двойной storage-контур (JSON + DB) создает рассинхронизацию и техдолг.
- Недостаточно маркетинговых метрик для адаптивных AI-решений.
- Graph UX не доведен для very-large networks (10k+).
- Realtime слой базовый, нет полноценного broadcast manager.
- Параллельное существование legacy и modern UI.
- Недозакрыт production hardening (observability, ротация, алерты).

### Приоритетный порядок
1. Маркетинговые метрики + decision engine.
2. Унификация storage-контуров.
3. Оптимизация graph UX + realtime broadcast.
4. Консолидация UI.
5. Production hardening.
6. Predictive/ML слой и авто-рекомендации.

---

## 2. Маркетинговые метрики (Core)

AI-агент должен принимать решения на данных, а не только на правилах.

### 5 ключевых метрик
1. **Engagement Score** (вовлеченность):
   - reply_rate, mention_frequency, response_time_factor, discussion_depth.
2. **Influence Score** (влиятельность):
   - pagerank, reach, reply_rate, sentiment_shift.
3. **Retention Score** (удержание):
   - days_active, activity_streak, recency, content_quality.
   - churn_risk = 1 - retention.
4. **Viral Coefficient** (виральность):
   - вклад в рост/приток внимания.
5. **Chat Health Score** (здоровье чата):
   - агрегат engagement/retention/toxicity/diversity.

### Где применять
- Адаптивная модерация.
- Раннее выявление churn-risk пользователей.
- Выделение лидеров мнений и viral contributors.
- Рекомендации администраторам.
- Feedback loop (результат действий -> корректировка стратегии).

---

## 3. AI Decision Framework

### Принцип
Для каждого события (сообщение/ответ/реакция):
1. Собрать контекст: sentiment, political score, user metrics, chat health.
2. Выбрать стратегию (gentle/standard/strict, мотивирующая, retention-first).
3. Выполнить действие (reply/emoji/warn/dm).
4. Логировать решение и outcome.
5. Обновить метрики и применить feedback.

### Обязательные сценарии
- At-risk user retention intervention.
- Адаптивная модерация с учетом influence/health.
- Viral moment detection и стратегия реакции.

---

## 3A. Topic-Agnostic Reaction Architecture

Политика должна остаться только первым активным профилем, а не жестко зашитой бизнес-логикой.

### Решение
- Ввести **реестр тематик** (topic policies) с параметрами:
  - `enabled` (вкл/выкл),
  - `action` (`moderate` / `observe`),
  - `priority`,
  - `keywords`,
  - `label/description`.
- Ввести **primary_moderation_topic** как настройку, переключаемую без правок кода.
- Считать три класса совпадений:
  - `matched_topics` (все совпавшие),
  - `moderation_topics` (только те, где `action=moderate`),
  - `trigger_topic` (приоритетный topic для реакции).
- Любую тему можно включить/отключить и сбросить в дефолт.

### Принципы совместимости
- Дефолтный профиль: `politics` (поведение backward-compatible).
- Для новых тем включается тот же orchestration pipeline (анализ -> стратегия -> действие), но через конфигурируемые policy-слои.
- Метрики и аудит решений сохраняют контекст `topic`, чтобы в будущем учить модель по разным доменам.

### UI/API минимум
- API управления topic policies (read/update/reset).
- Отдельные виджеты в админке:
  - активные темы,
  - какой topic сейчас primary,
  - какие темы только мониторятся (`observe`).

---

## 4. Unified Storage Strategy

### Целевая архитектура
- Source of truth: PostgreSQL (prod) / SQLite (dev).
- Основные сущности:
  - users
  - messages
  - graph_edges
  - metrics_snapshots
  - chat_settings

### План миграции
1. Подготовка схемы + ORM + миграции.
2. Миграция JSON -> DB.
3. Параллельная запись (dual-write) с проверкой целостности.
4. Cutover на DB.
5. Архивирование legacy JSON как historical backup.

---

## 5. Roadmap (фазы)

### Фаза 1 (2 недели): Metrics + Decision Engine
- MarketingMetricsService + realtime сбор.
- DecisionEngine + интеграция в bot message flow.
- Churn/viral/recommendations в админке.

### Фаза 2 (3 недели): Storage Unification
- DB schema + Alembic + async drivers.
- JSON -> DB migration + validation.
- Cache/query optimization.

### Фаза 3 (2 недели): Graph Optimization + Realtime UX
- NetworkX ядро графа.
- Продвинутые алгоритмы кластеров/центральности.
- D3/WebGL pipeline для больших графов.
- Broadcast manager + delta updates.

### Фаза 4 (2 недели): Production Hardening
- Prometheus/логирование/алерты.
- Security hardening (rate limit, input validation, auth/audit).
- CI/CD и staging -> prod pipeline.

### Фаза 5 (2 недели): Learning Layer
- Feedback loop и качество решений.
- Predictive models (churn/toxicity/virality).
- Auto-recommendations и optimization.

---

## 6. KPI целевого состояния

- Engagement: рост до ~0.75.
- Retention: рост до ~0.78.
- Churn rate: снижение до ~8%/мес.
- Time-to-decision админа: <10 сек с AI рекомендациями.
- Графы: стабильная работа на very-large chats.
- AI decision approval rate: >85%.

---

## 7. Риск-матрица (кратко)

- DB миграция ломает прод: mitigations -> dual-write + rollback.
- Ошибки AI решений: mitigations -> admin feedback + A/B + audit trail.
- Граф не масштабируется: mitigations -> WebGL, lazy loading, caching.
- Realtime backlog: mitigations -> Redis pub/sub + backpressure.

---

## 8. Правила исполнения для AI Agent

- Этот документ является рабочей спецификацией развития.
- Каждая фаза завершается конкретными deliverables и проверками.
- Изменения фиксируются в git регулярно (маленькими коммитами).
- Перед merge в master обязателен smoke + test pass.

