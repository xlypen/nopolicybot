#!/bin/bash
# Просмотр логов бота: личка, апдейты, необработанные.
# Использование: ./scripts/check_bot_logs.sh [число_строк]
N="${1:-200}"
journalctl -u telegram-bot -n "$N" --no-pager 2>/dev/null | grep -E "личка|Update|not handled|Пропуск ответа|Чат.*ответ" || true
