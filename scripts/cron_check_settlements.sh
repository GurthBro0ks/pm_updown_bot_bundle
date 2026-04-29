#!/usr/bin/env bash
# Settlement checker — runs every 30 minutes via cron
# Cron line: */30 * * * * /opt/slimy/pm_updown_bot_bundle/scripts/cron_check_settlements.sh
set -euo pipefail

cd /opt/slimy/pm_updown_bot_bundle
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

LOG="logs/settlement_check_$(date +%Y%m%d_%H%M%S).log"

./venv/bin/python3 scripts/check_settlements.py 2>&1 | tee "$LOG"

# Prune old settlement logs (keep 7 days)
find logs/ -name "settlement_check_*.log" -mtime +7 -delete 2>/dev/null || true
