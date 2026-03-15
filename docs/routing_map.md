# Routing Map — nopolicybot

> **Правило:** При добавлении нового endpoint — сначала обновить этот файл, потом писать код.  
> Полный чеклист: [docs/new_endpoint_checklist.md](new_endpoint_checklist.md)

## Правила чтения таблицы

- **nginx upstream** — куда nginx проксирует запрос (flask:5000 / fastapi:8001)
- **auth** — способ аутентификации: `session` (Flask), `bearer` (FastAPI), `none`
- **owner** — кто обрабатывает запрос в итоге: `flask` / `fastapi`

## Таблица маршрутов

| Путь | nginx upstream | auth | owner | Описание |
|------|----------------|------|-------|----------|
| / | flask:5000 | session | flask | Admin dashboard, HTML |
| /login | flask:5000 | none | flask | Страница логина |
| /admin/* | flask:5000 | session | flask | Admin UI |
| /static/* | flask:5000 / alias | none | flask | Статика |
| /api/* | flask:5000 | session | flask | Legacy Flask API |
| /api/v2/health | fastapi:8001 | none | fastapi | Healthcheck (мониторинг) |
| /api/v2/realtime/ws/* | fastapi:8001 | bearer | fastapi | WebSocket (прямой) |
| /api/v2/graph/* | flask:5000→fastapi:8001 | session | fastapi | Граф (через Flask-прокси) |
| /api/v2/admin/* | flask:5000→fastapi:8001 | session | fastapi | Admin API (через Flask-прокси) |
| /api/v2/recommendations | flask:5000→fastapi:8001 | session | fastapi | Рекомендации (через Flask-прокси) |
| /api/v2/predictive/* | flask:5000→fastapi:8001 | session | fastapi | Predictive (через Flask-прокси) |
| /api/v2/storage/* | flask:5000→fastapi:8001 | session | fastapi | Storage (через Flask-прокси) |
| /api/v2/metrics/* | flask:5000→fastapi:8001 | session | fastapi | Метрики (через Flask-прокси) |
| /api/v2/personality/* | flask:5000→fastapi:8001 | session | fastapi | Личность (через Flask-прокси) |
| /api/v2/portrait/* | flask:5000→fastapi:8001 | session | fastapi | Портреты (через Flask-прокси) |
| /api/v2/settings | flask:5000→fastapi:8001 | session | fastapi | Настройки (через Flask-прокси) |
| /api/v2/chat-mode | flask:5000→fastapi:8001 | session | fastapi | Режим чата (через Flask-прокси) |
| /api/v2/reset-political-count | flask:5000→fastapi:8001 | session | fastapi | Сброс счётчика (через Flask-прокси) |
| /api/v2/* (catch-all) | fastapi:8001 | bearer | fastapi | Остальное (docs, alerts, delete и т.д.) |

## Порты

- Flask: 5000
- FastAPI: 8001 (API_PORT)

## Схема

```
Браузер → nginx → Flask (проверяет сессию) → FastAPI (если нужно)
                → Flask (если endpoint в Flask)

WebSocket / health → nginx → FastAPI напрямую

Внешний API клиент → nginx → FastAPI напрямую (Bearer token)
```
