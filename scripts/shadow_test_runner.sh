#!/bin/bash
# Shadow Mode Test Runner
# Runs all phases in shadow mode, logs results, repeats

BOT_DIR="/opt/slimy/pm_updown_bot_bundle"
NOTES_DIR="$BOT_DIR/notes"
PROOFS_DIR="$BOT_DIR/proofs"
LOG_FILE="$BOT_DIR/logs/shadow_test.log"
CYCLES=${1:-8}  # Default 8 cycles (~2 hours at 15 min each)
BANKROLL=100
MAX_POS=10
INTERVAL=${2:-900}  # Default 15 min

mkdir -p "$NOTES_DIR" "$PROOFS_DIR"

echo "========================================"
echo "SHADOW MODE TEST"
echo "========================================"
echo "Cycles: $CYCLES"
echo "Interval: ${INTERVAL}s ($(($INTERVAL/60)) min)"
echo "Total time: ~$(($CYCLES * $INTERVAL / 3600)) hours"
echo "Started: $(date)"
echo "========================================"

for i in $(seq 1 $CYCLES); do
    echo ""
    echo "========================================"
    echo "CYCLE $i/$CYCLES - $(date)"
    echo "========================================"
    
    # Run bot in shadow mode
    cd "$BOT_DIR"
    
    # Run Phase 1
    echo "Running Phase 1 (Kalshi)..."
    python3 strategies/kalshi_optimize.py --mode shadow --bankroll $BANKROLL --max-pos $MAX_POS 2>&1 | tee -a "$LOG_FILE"
    
    # Run Phase 2
    echo "Running Phase 2 (SEF)..."
    python3 strategies/sef_spot_trading.py --mode shadow --bankroll $BANKROLL --max-pos $MAX_POS 2>&1 | tee -a "$LOG_FILE"
    
    # Run Phase 3
    echo "Running Phase 3 (Stock Hunter)..."
    python3 strategies/stock_hunter.py --mode shadow --bankroll $BANKROLL --max-pos $MAX_POS 2>&1 | tee -a "$LOG_FILE"
    
    # Capture proof files generated
    LATEST_PROOF=$(ls -t $PROOFS_DIR/*.json 2>/dev/null | head -1)
    
    # Extract key metrics from proof
    if [ -f "$LATEST_PROOF" ]; then
        PHASE1_ORDERS=$(cat "$LATEST_PROOF" | jq '.data.phase1?.orders // [] | length' 2>/dev/null || echo "0")
        PHASE2_ORDERS=$(cat "$LATEST_PROOF" | jq '.data.phase2?.orders // [] | length' 2>/dev/null || echo "0")
        PHASE3_ORDERS=$(cat "$LATEST_PROOF" | jq '.data.orders // [] | length' 2>/dev/null || echo "0")
        
        echo ""
        echo "CYCLE $i RESULTS:"
        echo "  Phase 1 (Kalshi) orders: $PHASE1_ORDERS"
        echo "  Phase 2 (SEF) orders: $PHASE2_ORDERS"
        echo "  Phase 3 (Stock) orders: $PHASE3_ORDERS"
        echo "  Total orders: $((PHASE1_ORDERS + PHASE2_ORDERS + PHASE3_ORDERS))"
        echo ""
        
        # Log to notes
        cat >> "$NOTES_DIR/shadow_test_$(date +%Y%m%d).md" << EOF

### Cycle $i - $(date '+%H:%M:%S')

**Orders:**
- Phase 1 (Kalshi): $PHASE1_ORDERS
- Phase 2 (SEF): $PHASE2_ORDERS
- Phase 3 (Stocks): $PHASE3_ORDERS

**Proof:** $(basename $LATEST_PROOF)

EOF
        
        # Also log top picks if available
        if [ "$PHASE3_ORDERS" -gt 0 ]; then
            TOP_PICK=$(cat "$LATEST_PROOF" | jq -r '.data.orders[0].ticker // "N/A"' 2>/dev/null)
            TOP_SENTIMENT=$(cat "$LATEST_PROOF" | jq -r '.data.orders[0].sentiment // "N/A"' 2>/dev/null)
            echo "- Top pick: $TOP_PICK (sentiment: $TOP_SENTIMENT)" >> "$NOTES_DIR/shadow_test_$(date +%Y%m%d).md"
        fi
    fi
    
    # Wait for next cycle (unless last cycle)
    if [ $i -lt $CYCLES ]; then
        echo ""
        echo "Waiting ${INTERVAL}s until next cycle..."
        sleep $INTERVAL
    fi
done

echo ""
echo "========================================"
echo "SHADOW TEST COMPLETE"
echo "========================================"
echo "Ended: $(date)"
echo "Notes: $NOTES_DIR/shadow_test_$(date +%Y%m%d).md"
echo "Logs: $LOG_FILE"
