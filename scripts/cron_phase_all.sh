#!/bin/bash
# Cron wrapper for runner.py --phase all (shadow mode, all phases)
# Fixes: bare runner.py command (needs python3), .env not sourced, no venv activation
# Modelled after scripts/cron_micro_live.sh which runs reliably.
# Installed: 2026-04-22

export PATH="/opt/slimy/pm_updown_bot_bundle/venv/bin:$PATH"
cd /opt/slimy/pm_updown_bot_bundle

set -a
source .env 2>/dev/null
set +a

LOG="logs/cron_phase_all_$(date +%Y%m%d).log"

timeout 900 ./venv/bin/python3 runner.py --mode shadow --phase all \
    >> "$LOG" 2>&1

RC=$?
echo "[$(date -Is)] Cron phase-all exit code: $RC" >> "$LOG"

if [ "$RC" -eq 0 ]; then
    /opt/slimy/pm_updown_bot_bundle/push-sync.sh >> "$LOG" 2>&1
fi

exit $RC
