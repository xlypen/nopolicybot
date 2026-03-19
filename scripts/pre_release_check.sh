#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[pre-release] python executable not found: $PYTHON_BIN" >&2
  exit 2
fi

echo "[pre-release] root=$ROOT_DIR"
echo "[pre-release] using python=$PYTHON_BIN"

run_step() {
  local title="$1"
  shift
  echo
  echo "[pre-release] >>> $title"
  "$@"
}

run_step "Static compile check" \
  "$PYTHON_BIN" -m compileall -q api services tests scripts admin_app.py bot.py

run_step "Full test suite" \
  "$PYTHON_BIN" -m pytest -q

run_step "Smoke checks" \
  "$PYTHON_BIN" scripts/smoke_checks.py

run_step "SLO gate (docs/slo.md)" \
  "$PYTHON_BIN" scripts/check_slo_gate.py --slo-file docs/slo.md

run_step "Deploy script dry-run (staging)" \
  bash scripts/deploy_release.sh --target staging --dry-run

run_step "Deploy script dry-run (production)" \
  bash scripts/deploy_release.sh --target production --dry-run

echo
echo "[pre-release] all checks passed"
