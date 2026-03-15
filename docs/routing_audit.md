# Routing Audit — nopolicybot

Дата: 2025-03-14  
Цель: полная диагностика перед унификацией nginx и маршрутизации.

---

## 1. Nginx-конфиги в репозитории

| Файл | Назначение |
|------|------------|
| `deploy-ubuntu/nginx-nopolicybot.conf` | Полный конфиг: graph, admin, recommendations, predictive, storage, metrics, personality, portrait, settings, realtime |
| `deploy-ubuntu/nginx-site.conf` | Упрощённый: metrics, personality, portrait, settings, realtime; **нет** graph, admin, recommendations, predictive, storage |
| `deploy-ubuntu/logrotate-nopolicybot.conf` | Logrotate, не nginx |
| `deploy-ubuntu/install.sh` | Inline heredoc — генерирует конфиг при установке (с personality) |

---

## 2. Сравнение nginx-конфигов

### nginx-nopolicybot.conf (наиболее полный)
- `/api/v2/realtime/ws/` → 8001 (FastAPI, WebSocket)
- `/api/v2/graph/` → 5000 (Flask)
- `/api/v2/admin/` → 5000 (Flask)
- `/api/v2/recommendations` → 5000 (Flask)
- `/api/v2/predictive/` → 5000 (Flask)
- `/api/v2/storage/` → 5000 (Flask)
- `/api/v2/metrics` → 5000 (Flask)
- `/api/v2/personality/` → 5000 (Flask)
- `/api/v2/portrait/` → 5000 (Flask)
- `=/api/v2/settings` → 5000 (Flask)
- `=/api/v2/chat-mode` → 5000 (Flask)
- `=/api/v2/reset-political-count` → 5000 (Flask)
- `/api/v2/` (catch-all) → 8001 (FastAPI)
- `/` → 5000 (Flask)

### nginx-site.conf
- Отсутствуют: graph, admin, recommendations, predictive, storage
- Остальное совпадает с nopolicybot

### install.sh (inline)
- Совпадает с nopolicybot (после последних правок)

---

## 3. Flask-прокси на FastAPI (admin_app.py)

Все используют `@login_required` + `_proxy_to_api_v2(path, method, data)`:

| Маршрут | Методы | Проксирует на |
|---------|--------|---------------|
| `/api/v2/graph/<path>` | GET | FastAPI |
| `/api/v2/admin/<path>` | GET, POST | FastAPI |
| `/api/v2/recommendations` | GET, POST | FastAPI |
| `/api/v2/predictive/<path>` | GET | FastAPI |
| `/api/v2/storage/<path>` | GET, POST | FastAPI |
| `/api/v2/metrics/<path>` | GET | FastAPI |
| `/api/v2/personality/<path>` | GET, POST | FastAPI |
| `/api/v2/portrait/<path>` | GET, POST | FastAPI |
| `/api/v2/settings` | GET, POST | FastAPI |
| `/api/v2/chat-mode` | GET, POST | FastAPI |
| `/api/v2/reset-political-count` | POST | FastAPI |

`proxy_to_fastapi` добавляет Bearer (ADMIN_TOKEN), URL: `http://127.0.0.1:{API_PORT}` (по умолчанию 8001).

---

## 4. FastAPI маршруты (api/main.py + routers)

| Prefix | Router | Endpoints |
|--------|--------|-----------|
| `/api/v2` | health | GET /health |
| `/api/v2` | (inline) | GET /metrics, GET /alerts, DELETE /users/{id}/data |
| `/api/v2/graph` | graph | GET /{chat_id}, GET /{chat_id}/delta |
| `/api/v2/admin` | admin | dashboard, community-structure, leaderboard, at-risk-users, at-risk-action, log-tail, prompts, topic-policies |
| `/api/v2/metrics` | metrics | GET /user/{id}, GET /chat/{id}/health |
| `/api/v2/personality` | personality | GET /user/{id}, GET /user/{id}/history, GET /user/{id}/verify, GET /user/{id}/drift, POST /compare, POST /build, GET /community/{id}/clusters |
| `/api/v2/portrait` | portrait | portrait-from-storage, portrait-classify-unknown, portrait-building-status, user/{id}/portrait-image, portrait-clear-cache |
| `/api/v2/recommendations` | recommendations | GET "", POST /mark-done |
| `/api/v2/predictive` | predictive | GET /overview |
| `/api/v2/settings` | settings | GET/POST /settings, GET/POST /chat-mode, POST /reset-political-count |
| `/api/v2/storage` | storage | GET /status, GET /cutover-report, POST /cutover |
| `/api/v2/realtime` | realtime | WebSocket |

---

## 5. Аутентификация

| Где | Механизм |
|-----|-----------|
| Flask | `@login_required` + `session` |
| FastAPI | `require_auth` = HTTPBearer (ADMIN_TOKEN) |
| Flask→FastAPI | `proxy_to_fastapi` добавляет `Authorization: Bearer {ADMIN_TOKEN}` |

Порты: Flask 5000, FastAPI 8001 (API_PORT).

---

## 6. Выводы

1. **Разные конфиги**: nopolicybot полный, nginx-site урезан, install.sh — свой inline.
2. **Несоответствие**: nginx-site не содержит graph, admin, recommendations, predictive, storage — при его использовании эти endpoints идут в catch-all `/api/v2/` → FastAPI и не проходят через Flask (401 без Bearer).
3. **Единый источник**: нужен один канонический конфиг, из которого генерируются все варианты.
