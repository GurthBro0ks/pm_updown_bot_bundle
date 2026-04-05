#!/bin/bash
# Shadow mode run for 2 hours
# Runs trading bot + arb detection every 5 minutes
# v2 - Added signal handling and error recovery

DURATION_SECONDS=$((2 * 60 * 60))  # 2 hours
INTERVAL_SECONDS=300  # 5 minutes between runs
START_TIME=$(date +%s)
END_TIME=$((START_TIME + DURATION_SECONDS))
RUN_COUNT=0
LOG_FILE="/opt/slimy/pm_updown_bot_bundle/logs/shadow_2h_$(date +%Y%m%d_%H%M%S).log"

# Signal handler - log when killed
cleanup() {
    echo "" | tee -a "$LOG_FILE"
    echo "[SIGNAL] Script interrupted at $(date)" | tee -a "$LOG_FILE"
    echo "Total runs completed: $RUN_COUNT" | tee -a "$LOG_FILE"
    exit 1
}

trap cleanup SIGTERM SIGINT SIGHUP

echo "========================================" | tee -a "$LOG_FILE"
echo "SHADOW MODE - 2 HOUR RUN (v2)" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "Duration: 2 hours" | tee -a "$LOG_FILE"
echo "Interval: 5 minutes" | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "PID: $$" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

cd /opt/slimy/pm_updown_bot_bundle

while [ $(date +%s) -lt $END_TIME ]; do
    RUN_COUNT=$((RUN_COUNT + 1))
    ELAPSED=$(( ($(date +%s) - START_TIME) / 60 ))
    REMAINING=$(( (END_TIME - $(date +%s)) / 60 ))
    
    echo "" | tee -a "$LOG_FILE"
    echo "[$(date)] Run #$RUN_COUNT | Elapsed: ${ELAPSED}min | Remaining: ${REMAINING}min" | tee -a "$LOG_FILE"
    echo "----------------------------------------" | tee -a "$LOG_FILE"
    
    # Run all phases in shadow mode with timeout protection
    # Max 4 minutes per run to prevent hangs
    timeout 240 python3 runner.py --phase all --mode shadow --bankroll 100 --max-pos 10 2>&1 | tee -a "$LOG_FILE"
    
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 124 ]; then
        echo "Run #$RUN_COUNT TIMED OUT (240s limit)" | tee -a "$LOG_FILE"
    else
        echo "Run #$RUN_COUNT exit code: $EXIT_CODE" | tee -a "$LOG_FILE"
    fi
    
    # Check if we have time for another run
    if [ $(date +%s) -ge $END_TIME ]; then
        break
    fi
    
    echo "Sleeping for ${INTERVAL_SECONDS}s..." | tee -a "$LOG_FILE"
    sleep $INTERVAL_SECONDS
done

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "SHADOW RUN COMPLETE" | tee -a "$LOG_FILE"
echo "Ended: $(date)" | tee -a "$LOG_FILE"
echo "Total runs: $RUN_COUNT" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
