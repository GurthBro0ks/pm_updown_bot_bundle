#!/usr/bin/env bash
# Weather market scanner — runs every 2 hours
# Cron line sets WEATHER_DRY_RUN and the weather safety limits inline:
#   0 */2 * * * WEATHER_DRY_RUN=false WEATHER_MAX_ORDERS_PER_RUN=2 WEATHER_MAX_DAILY_LOSS_USD=1.00 WEATHER_MAX_NOTIONAL_PER_RUN_USD=1.00 WEATHER_MIN_TRADE_PRICE_CENTS=25 /opt/slimy/pm_updown_bot_bundle/scripts/cron_weather_trade.sh
# WEATHER_DRY_RUN=true → pass --dry-run; false → run live.
# More frequent than the main bot because weather markets are time-sensitive
set -euo pipefail

cd /opt/slimy/pm_updown_bot_bundle
export $(grep -v '^#' .env | xargs) 2>/dev/null || true
WEATHER_DRY_RUN="${WEATHER_DRY_RUN:-true}"
export WEATHER_DRY_RUN

# Weather safety limits — independent from the main bot's limits.
# Cron-line values win; these are conservative fallbacks if unset.
export WEATHER_MAX_ORDERS_PER_RUN="${WEATHER_MAX_ORDERS_PER_RUN:-2}"
export WEATHER_MAX_DAILY_LOSS_USD="${WEATHER_MAX_DAILY_LOSS_USD:-1.00}"
export WEATHER_MAX_NOTIONAL_PER_RUN_USD="${WEATHER_MAX_NOTIONAL_PER_RUN_USD:-1.00}"
export WEATHER_MIN_TRADE_PRICE_CENTS="${WEATHER_MIN_TRADE_PRICE_CENTS:-25}"
export WEATHER_MAX_EXPOSURE_PER_CITY="${WEATHER_MAX_EXPOSURE_PER_CITY:-1.00}"

LOG="logs/weather_trade_$(date +%Y%m%d_%H%M%S).log"

ARGS=()
if [[ "${WEATHER_DRY_RUN,,}" == "true" || "$WEATHER_DRY_RUN" == "1" || "${WEATHER_DRY_RUN,,}" == "yes" ]]; then
  ARGS+=(--dry-run)
fi

./venv/bin/python3 scripts/run_weather_strategy.py "${ARGS[@]}" 2>&1 | tee "$LOG"

# Prune old logs (keep 7 days)
find logs/ -name "weather_trade_*.log" -mtime +7 -delete 2>/dev/null || true
find logs/ -name "weather_strategy_*.log" -mtime +7 -delete 2>/dev/null || true
