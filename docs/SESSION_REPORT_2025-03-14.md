# Отчёт за 14 марта 2025

## Блок A (STRATEGIC_DEVELOPMENT_PLAN) — завершён

Выполнены этапы A1–A6 по порядку, с коммитами и тестами после каждого.

| Этап | Коммит | Описание |
|------|--------|----------|
| A1 | `a4b645f` | SLO baseline — SLO gate в CI smoke job |
| A2 | `deaacce` | Gunicorn workers — устранение SPOF admin-сервиса |
| A3 | `9d96559` | Coverage gate в CI |
| A4 | `460c477` | FastAPI lifespan migration — verified complete |
| A5 | `da5e483` | DB cutover — отключение JSON write при db_only |
| A6 | `df362be` | Realtime WebSocket hardening — verified complete |

### A5: изменения
- `services/storage_cutover.py` — добавлена `storage_json_writes_enabled()` (False при db_only)
- `user_stats._save`, `social_graph._save`, `services/marketing_metrics._save_data` — проверка перед записью в JSON
- В `tests/unit/test_marketing_metrics.py` — monkeypatch `storage_json_writes_enabled` → True в фикстуре

### A6: подтверждено
- BroadcastManager: slow_warn_threshold=0.8, graceful disconnect (1008), ws_queue_utilization в Prometheus, Redis Pub/Sub

---

## Fix: HTTP 401 на профиле пользователя

**Проблема:** На странице «Профиль пользователя» — «Ошибка портрета: HTTP 401», 401 на `/api/v2/metrics/user/...` и `/api/v2/portrait/portrait-from-storage`.

**Причина:** Nginx направлял `/api/v2/metrics/` и `/api/v2/portrait/` напрямую в FastAPI (8001). FastAPI требует Bearer-токен, фронтенд его не отправляет.

**Решение:** Маршрутизация этих путей через Flask (5000), который проверяет сессию и проксирует в FastAPI с Bearer-токеном.

**Коммит:** `f17629c` — fix: route /api/v2/metrics and /api/v2/portrait through Flask proxy

**Изменённые файлы:**
- `deploy-ubuntu/nginx-nopolicybot.conf` — location для metrics, portrait, settings, chat-mode, reset-political-count → 5000
- `deploy-ubuntu/nginx-site.conf` — то же
- `deploy-ubuntu/install.sh` — обновлён встроенный nginx-конфиг

**После деплоя:** `sudo nginx -t && sudo systemctl reload nginx`

---

## Исправлено: интеграционные тесты

- `test_api_chat_mode_get_contract`, `test_api_chat_mode_post_contract`
- `test_api_metrics_chat_health_contract`
- `test_api_portrait_classify_unknown_contract`

**Причина:** Legacy-маршруты проксируют в FastAPI v2; тесты мокали сервисы, но вызывался прокси. **Решение:** мокать `_proxy_to_api_v2` вместо bot_settings/marketing_metrics/user_stats.

---

## Ключевые пути

- `.github/workflows/ci.yml` — SLO gate, coverage
- `deploy-ubuntu/` — Gunicorn, nginx
- `services/storage_cutover.py` — `storage_json_writes_enabled()`
- `docs/STAGE_T_AGENT_ACTION_PLAN.md` — статусы A4, A6
