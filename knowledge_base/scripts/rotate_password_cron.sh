#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 実行ファイルが見つかりません: $PYTHON_BIN" >&2
  exit 1
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"

USERNAME="${ROTATE_USERNAME:-cs-demo}"
RECIPIENT_EMAIL="${ROTATE_RECIPIENT_EMAIL:-kuwaharata@systena.co.jp}"
PASSWORD_LENGTH="${ROTATE_PASSWORD_LENGTH:-12}"
DRY_RUN="${ROTATE_DRY_RUN:-false}"

ARGS=(
  manage.py rotate_user_password
  --username "$USERNAME"
  --recipient-email "$RECIPIENT_EMAIL"
  --password-length "$PASSWORD_LENGTH"
)

if [ "$DRY_RUN" = "true" ]; then
  ARGS+=(--dry-run)
fi

"$PYTHON_BIN" "${ARGS[@]}"
