#!/usr/bin/env bash
# Weather market scanner — runs every 2 hours.
# Current policy: weather cron must stay dry-run until security cleanup, full
# tests, operator QA, and Claude safety closeout all pass.
#
# Dry-run cron line:
#   0 */2 * * * WEATHER_DRY_RUN=true /opt/slimy/pm_updown_bot_bundle/scripts/cron_weather_trade.sh
#
# Future live cron line requires a separate activation prompt:
#   0 */2 * * * WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true WEATHER_MAX_ORDERS_PER_RUN=1 WEATHER_MAX_ORDER_USD=0.25 WEATHER_MAX_RUN_EXPOSURE_USD=1.00 WEATHER_MAX_OPEN_EXPOSURE_USD=2.00 WEATHER_MAX_DAILY_LOSS_USD=1.00 WEATHER_MIN_TRADE_PRICE_CENTS=25 /opt/slimy/pm_updown_bot_bundle/scripts/cron_weather_trade.sh
#
# WEATHER_DRY_RUN=true/unset → pass --dry-run.
# WEATHER_DRY_RUN=false without WEATHER_LIVE_ENABLED=true still dry-runs.
# Live mode requires both WEATHER_DRY_RUN=false and WEATHER_LIVE_ENABLED=true.
# More frequent than the main bot because weather markets are time-sensitive
set -euo pipefail

cd /opt/slimy/pm_updown_bot_bundle
export $(grep -v '^#' .env | xargs) 2>/dev/null || true
WEATHER_DRY_RUN="${WEATHER_DRY_RUN:-true}"
export WEATHER_DRY_RUN

# Weather safety limits — independent from the main bot's limits.
# Dry-run gets conservative defaults. Live mode must provide every tiny-limit
# variable explicitly on the weather cron line.
if [[ "${WEATHER_DRY_RUN,,}" == "false" && "${WEATHER_LIVE_ENABLED:-}" == "true" ]]; then
  : "${WEATHER_MAX_ORDERS_PER_RUN:?WEATHER_MAX_ORDERS_PER_RUN required for live weather}"
  : "${WEATHER_MAX_ORDER_USD:?WEATHER_MAX_ORDER_USD required for live weather}"
  : "${WEATHER_MAX_RUN_EXPOSURE_USD:?WEATHER_MAX_RUN_EXPOSURE_USD required for live weather}"
  : "${WEATHER_MAX_OPEN_EXPOSURE_USD:?WEATHER_MAX_OPEN_EXPOSURE_USD required for live weather}"
else
  export WEATHER_MAX_ORDERS_PER_RUN="${WEATHER_MAX_ORDERS_PER_RUN:-1}"
  export WEATHER_MAX_ORDER_USD="${WEATHER_MAX_ORDER_USD:-0.25}"
  export WEATHER_MAX_RUN_EXPOSURE_USD="${WEATHER_MAX_RUN_EXPOSURE_USD:-1.00}"
  export WEATHER_MAX_OPEN_EXPOSURE_USD="${WEATHER_MAX_OPEN_EXPOSURE_USD:-2.00}"
fi
export WEATHER_MAX_DAILY_LOSS_USD="${WEATHER_MAX_DAILY_LOSS_USD:-1.00}"
export WEATHER_MIN_TRADE_PRICE_CENTS="${WEATHER_MIN_TRADE_PRICE_CENTS:-25}"
export WEATHER_MAX_EXPOSURE_PER_CITY="${WEATHER_MAX_EXPOSURE_PER_CITY:-1.00}"

LOG="logs/weather_trade_$(date +%Y%m%d_%H%M%S).log"

ARGS=()
if [[ "${WEATHER_DRY_RUN,,}" != "false" || "${WEATHER_LIVE_ENABLED:-}" != "true" ]]; then
  ARGS+=(--dry-run)
fi

./venv/bin/python3 scripts/run_weather_strategy.py "${ARGS[@]}" 2>&1 | tee "$LOG"

# Prune old logs (keep 7 days)
find logs/ -name "weather_trade_*.log" -mtime +7 -delete 2>/dev/null || true
find logs/ -name "weather_strategy_*.log" -mtime +7 -delete 2>/dev/null || true
