# Data Map and Retention

Текущая карта данных `nopolicybot` для контроля PII и retention-политик.

| Тип данных | Где хранится | Retention | PII |
|---|---|---|---|
| Telegram user id, display_name, username, first/last name | `db.users`, `user_stats.json` | до удаления по `DELETE /api/v2/users/{user_id}/data` | yes |
| Raw message text | `db.messages.text`, `user_stats.json -> users[*].messages_by_chat`, `social_graph.json -> dialogue_log` | авто-удаление старше `MESSAGE_RETENTION_DAYS` (по умолчанию 90) | yes |
| Message metadata (chat_id, sent_at, media_type, replied_to) | `db.messages` | авто-удаление старше `MESSAGE_RETENTION_DAYS` | partial |
| Graph edges (user-to-user interactions) | `db.edges`, `social_graph.json -> connections` | до удаления пользователя или ручного cutover/cleanup | yes |
| User portraits, image descriptors, close-attention views | `user_stats.json`, `db.user_portraits` | до удаления пользователя | yes |
| Moderation/decision events | `data/decision_events.json` | rolling app-level history | partial |
| Security and runtime audit events | `data/audit_events.jsonl` | rolling app-level history | partial |
| Health/metrics snapshots | runtime memory + metrics endpoints | ephemeral/runtime | no |

## Right to Erasure

- API endpoint: `DELETE /api/v2/users/{user_id}/data` (auth required).
- Endpoint удаляет/редактирует данные пользователя в DB и JSON-conтурах:
  - DB: `users`, `messages` (author rows), `edges`, `user_portraits`, `replied_to` references.
  - JSON: `user_stats.json` user entry, `social_graph.json` пары и диалоговые события с участием пользователя.

## Retention Job

- Фоновая задача: `data_retention_task` (бот-сервис).
- Источники очистки: DB messages + JSON raw logs.
- Параметры:
  - `MESSAGE_RETENTION_DAYS` (default `90`)
  - `RETENTION_CHECK_INTERVAL_SEC` (default `21600`, 6h)
