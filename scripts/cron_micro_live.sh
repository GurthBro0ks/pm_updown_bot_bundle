#!/usr/bin/env bash
# Cron wrapper for micro-live trading
# Run: crontab -e → 0 */4 * * * /opt/slimy/pm_updown_bot_bundle/scripts/cron_micro_live.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

fail() {
  echo "[MICRO_CRON] FAIL: $*" >&2
  exit 1
}

preserve_runtime_override() {
  local name="$1"
  if [ "${!name+x}" = "x" ]; then
    PRESERVED_RUNTIME_NAMES+=("$name")
    PRESERVED_RUNTIME_VALUES+=("${!name}")
  fi
}

restore_runtime_overrides() {
  local i name value
  for i in "${!PRESERVED_RUNTIME_NAMES[@]}"; do
    name="${PRESERVED_RUNTIME_NAMES[$i]}"
    value="${PRESERVED_RUNTIME_VALUES[$i]}"
    export "$name=$value"
  done
}

PRESERVED_RUNTIME_NAMES=()
PRESERVED_RUNTIME_VALUES=()

preserve_runtime_override "TRADING_PAUSED"
preserve_runtime_override "MAX_ORDERS_PER_RUN"
preserve_runtime_override "MAX_DAILY_LOSS_USD"
preserve_runtime_override "MAX_NOTIONAL_PER_RUN_USD"
preserve_runtime_override "MIN_TRADE_PRICE_CENTS"
preserve_runtime_override "MAX_DAYS_TO_EXPIRY"
preserve_runtime_override "KALSHI_ALLOWED_CATEGORIES"

export PATH="$REPO_ROOT/venv/bin:$PATH"
cd "$REPO_ROOT"

if [ ! -r "$ENV_FILE" ]; then
  fail "env file missing or unreadable: $ENV_FILE"
fi

had_xtrace=0
case "$-" in
  *x*) had_xtrace=1; set +x ;;
esac
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
if [ "$had_xtrace" -eq 1 ]; then
  set -x
fi
restore_runtime_overrides

export DEBATE_MODE=true

# Run with timeout (10.5 min max)
timeout 630 ./venv/bin/python3 runner.py --mode micro-live --phase phase1 \
    >> logs/cron_micro_live.log 2>&1

# Log completion
echo "[$(date -Is)] Cron micro-live exit code: $?" >> logs/cron_micro_live.log
