#!/usr/bin/env bash
set -euo pipefail

# Hourly Shadow Runner Wrapper
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

fail() {
  echo "[SHADOW] FAIL: $*" >&2
  exit 1
}

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    fail "missing required env var: $name"
  fi
  echo "[SHADOW] PASS: required env var present: $name" >&2
}

require_readable_file_env() {
  local name="$1"
  require_env "$name"
  if [ ! -r "${!name}" ]; then
    fail "file referenced by $name is missing or unreadable"
  fi
  echo "[SHADOW] PASS: readable file referenced by: $name" >&2
}

cd "$REPO_ROOT"

if [ ! -r "$ENV_FILE" ]; then
  fail "env file missing or unreadable: $ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

require_env KALSHI_KEY
require_readable_file_env KALSHI_SECRET_FILE

python3 scripts/hourly_shadow.py >> logs/hourly.log 2>&1
