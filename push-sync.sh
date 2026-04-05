#!/bin/bash
# Push sync to NUC2 after bot runs
# Called by cron after runner.py completes

source /opt/slimy/pm_updown_bot_bundle/.env

NUC2_WEBHOOK="http://100.105.119.62:3000/api/webhook/bot-sync"
LOG="/opt/slimy/pm_updown_bot_bundle/logs/push-sync.log"

if [ -z "$BOT_SYNC_SECRET" ]; then
  echo "$(date -Iseconds) ERROR: BOT_SYNC_SECRET not set" >> "$LOG"
  exit 1
fi

RESULT=$(curl -s --connect-timeout 10 --max-time 20 \
  -X POST "$NUC2_WEBHOOK" \
  -H "Authorization: Bearer $BOT_SYNC_SECRET" 2>/dev/null)

SYNCED=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('synced',0))" 2>/dev/null || echo "FAIL")

echo "$(date -Iseconds) synced=$SYNCED result=$RESULT" >> "$LOG"
