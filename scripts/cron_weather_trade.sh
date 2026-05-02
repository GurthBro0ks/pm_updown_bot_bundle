#!/usr/bin/env bash
# Weather market scanner — runs every 2 hours
# Cron line: 0 */2 * * * /opt/slimy/pm_updown_bot_bundle/scripts/cron_weather_trade.sh
# More frequent than the main bot because weather markets are time-sensitive
set -euo pipefail

cd /opt/slimy/pm_updown_bot_bundle
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

LOG="logs/weather_trade_$(date +%Y%m%d_%H%M%S).log"

./venv/bin/python3 scripts/run_weather_strategy.py 2>&1 | tee "$LOG"

# Prune old logs (keep 7 days)
find logs/ -name "weather_trade_*.log" -mtime +7 -delete 2>/dev/null || true
find logs/ -name "weather_strategy_*.log" -mtime +7 -delete 2>/dev/null || true
