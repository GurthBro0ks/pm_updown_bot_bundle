#!/bin/bash
# Cron wrapper for micro-live trading
# Run: crontab -e → 0 */4 * * * /opt/slimy/pm_updown_bot_bundle/scripts/cron_micro_live.sh

export PATH="/opt/slimy/pm_updown_bot_bundle/venv/bin:$PATH"
cd /opt/slimy/pm_updown_bot_bundle

# Source environment
set -a
source .env 2>/dev/null
set +a
export DEBATE_MODE=true

# Run with timeout (10 min max)
timeout 600 ./venv/bin/python3 runner.py --mode micro-live --phase phase1 \
    >> logs/cron_micro_live.log 2>&1

# Log completion
echo "[$(date -Is)] Cron micro-live exit code: $?" >> logs/cron_micro_live.log