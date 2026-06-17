#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BASE_DIR="${APP_DIR:-}"
if [ -z "$BASE_DIR" ]; then
  if [ -f "/home/site/wwwroot/manage.py" ]; then
    BASE_DIR="/home/site/wwwroot"
  else
    BASE_DIR="$DEFAULT_BASE_DIR"
  fi
fi

if [ ! -f "$BASE_DIR/manage.py" ]; then
  DETECTED_DIR="$(find /home/site /tmp -maxdepth 4 -name manage.py 2>/dev/null | head -n1 | xargs dirname || true)"
  if [ -n "$DETECTED_DIR" ] && [ -f "$DETECTED_DIR/manage.py" ]; then
    BASE_DIR="$DETECTED_DIR"
  fi
fi

if [ ! -f "$BASE_DIR/manage.py" ]; then
  echo "manage.py が見つかりません。APP_DIR を指定してください。" >&2
  exit 1
fi

CRON_ENV_FILE="${CRON_ENV_FILE:-/home/site/wwwroot/.cron_db_env}"
if [ -f "$CRON_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$CRON_ENV_FILE"
  set +a
fi

cd "$BASE_DIR"

PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
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
  --password-length "$PASSWORD_LENGTH"
)

if [ "$DRY_RUN" = "true" ]; then
  ARGS+=(--dry-run)
fi

# メール送信：ROTATE_SEND_EMAIL=true の場合のみ送信。既定はスキップ。
if [ "${ROTATE_SEND_EMAIL:-false}" = "true" ]; then
  ARGS+=(--recipient-email "$RECIPIENT_EMAIL")
else
  ARGS+=(--no-email)
fi

"$PYTHON_BIN" "${ARGS[@]}"
