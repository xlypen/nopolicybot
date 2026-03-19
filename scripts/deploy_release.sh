#!/usr/bin/env bash
set -euo pipefail

TARGET="staging"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="${2:-staging}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$TARGET" != "staging" && "$TARGET" != "production" ]]; then
  echo "target must be staging|production" >&2
  exit 2
fi

DEPLOY_PATH="${DEPLOY_PATH:-/opt/telegram-political-monitor-bot}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-}"
if [[ -z "$DEPLOY_BRANCH" ]]; then
  if [[ "$TARGET" == "production" ]]; then
    DEPLOY_BRANCH="master"
  else
    DEPLOY_BRANCH="restore-from-archive"
  fi
fi

REMOTE_USER="${DEPLOY_USER:-root}"
REMOTE_HOST="${DEPLOY_HOST:-}"
REMOTE_PORT="${DEPLOY_PORT:-22}"
SSH_KEY="${DEPLOY_SSH_KEY:-}"

REMOTE_SCRIPT=$(cat <<'EOF'
set -euo pipefail
cd "$1"
git fetch origin
git checkout "$2"
git pull --ff-only origin "$2"
./venv/bin/python -m pytest -q tests/integration/test_fastapi_v2.py tests/integration/test_api_contracts.py
./venv/bin/python scripts/smoke_checks.py
if command -v sudo >/dev/null 2>&1; then
  sudo -n systemctl restart telegram-bot.service telegram-bot-admin.service telegram-bot-api.service || true
  sudo -n systemctl is-active telegram-bot.service || true
  sudo -n systemctl is-active telegram-bot-admin.service || true
  sudo -n systemctl is-active telegram-bot-api.service || true
else
  systemctl restart telegram-bot.service telegram-bot-admin.service telegram-bot-api.service || true
  systemctl is-active telegram-bot.service || true
  systemctl is-active telegram-bot-admin.service || true
  systemctl is-active telegram-bot-api.service || true
fi
EOF
)

echo "[deploy] target=${TARGET} branch=${DEPLOY_BRANCH} path=${DEPLOY_PATH}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[deploy] dry-run mode enabled"
  echo "ssh -p ${REMOTE_PORT} ${REMOTE_USER}@<host> 'bash -s -- ${DEPLOY_PATH} ${DEPLOY_BRANCH}' <<'SCRIPT'"
  echo "$REMOTE_SCRIPT"
  echo "SCRIPT"
  exit 0
fi

if [[ -z "$REMOTE_HOST" || -z "$SSH_KEY" ]]; then
  echo "DEPLOY_HOST and DEPLOY_SSH_KEY are required for non-dry-run deploy" >&2
  exit 2
fi

KEY_FILE="$(mktemp)"
chmod 600 "$KEY_FILE"
printf "%s\n" "$SSH_KEY" > "$KEY_FILE"
trap 'rm -f "$KEY_FILE"' EXIT

ssh -i "$KEY_FILE" -p "$REMOTE_PORT" -o StrictHostKeyChecking=no "${REMOTE_USER}@${REMOTE_HOST}" \
  "bash -s -- \"$DEPLOY_PATH\" \"$DEPLOY_BRANCH\"" <<<"$REMOTE_SCRIPT"

echo "[deploy] done"
