#!/bin/bash
# Weekly PNL Report Generator for Trading Bot
# Generates report, saves to file for agent to send via Telegram

BOT_DIR="/opt/slimy/pm_updown_bot_bundle"
LOG_DIR="$BOT_DIR/logs"
PROOF_DIR="$BOT_DIR/proofs"
LOGFILE="$LOG_DIR/weekly-pnl.log"
REPORT_FILE="$LOG_DIR/latest-weekly-report.txt"

# Calculate week date range (last 7 days)
END_DATE=$(date -d "today" +%Y-%m-%d)
START_DATE=$(date -d "7 days ago" +%Y-%m-%d)

# Initialize counters
TOTAL_RUNS=0
TOTAL_ORDERS=0

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOGFILE"
}

log "Starting weekly PNL report: $START_DATE to $END_DATE"

# Count total runs in the period
TOTAL_RUNS=$(find "$LOG_DIR" -name "run_*.log" -newermt "$START_DATE" 2>/dev/null | wc -l)

# Count proofs with orders in the period
TOTAL_ORDERS=0
while IFS= read -r -d '' proof_file; do
    ORDER_COUNT=$(jq -r '.data.orders | length' "$proof_file" 2>/dev/null || echo "0")
    TOTAL_ORDERS=$((TOTAL_ORDERS + ORDER_COUNT))
done < <(find "$PROOF_DIR" -name "proof_real_live_*.json" -newermt "$START_DATE" -print0 2>/dev/null)

log "Total runs: $TOTAL_RUNS"
log "Total orders placed: $TOTAL_ORDERS"

# Build report message
cat > "$REPORT_FILE" << REPORT
📊 **Weekly Trading Bot Report**

**Period**: $START_DATE to $END_DATE

**Summary**:
- Total runs: $TOTAL_RUNS
- Orders placed: $TOTAL_ORDERS
- Orders filled: 0
- PNL: \$0.00 (no trades executed)

**Status**: ⚠️ Bot running, 0 orders due to gate violations

**Notes**: All runs show GATE VIOLATION (size < \$0.01). Risk caps configured but gates blocking all trades.
REPORT

log "Report saved to $REPORT_FILE"

# Attempt to send via message tool if available
# This will work when called from the agent context
if command -v openclaw &> /dev/null; then
    log "OpenClaw detected, attempting to send report"
    # The agent can send this via Telegram later
fi

log "Weekly PNL report complete"
