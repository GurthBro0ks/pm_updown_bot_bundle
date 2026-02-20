#!/bin/bash
# Micro-Live Runner - Real trades with strict risk gates
# Usage: ./run-micro-live.sh [--phase phase1|phase2|phase3|all]

set -e

cd /opt/slimy/pm_updown_bot_bundle

# Load environment
source venv/bin/activate 2>/dev/null || true

# Micro-live risk caps (conservative)
export MAX_POS_USD=10
export MAX_DAILY_LOSS=50
export LIQUIDITY_MIN=1000
export EDGE_MIN_PCT=0.5
export MARKET_END_HRS=24

echo "=========================================="
echo "MICRO-LIVE MODE"
echo "=========================================="
echo "Max Position:     \$${MAX_POS_USD}"
echo "Max Daily Loss:   \$${MAX_DAILY_LOSS}"
echo "Min Liquidity:    \$${LIQUIDITY_MIN}"
echo "Min Edge:         ${EDGE_MIN_PCT}%"
echo "Min Hours Left:   ${MARKET_END_HRS}h"
echo "=========================================="
echo ""
echo "⚠️  REAL MONEY TRADES WITH RISK GATES"
echo "⚠️  Press Ctrl+C within 5s to abort"
echo ""

sleep 5

# Run with micro-live mode
PHASE=${1:-all}

python3 runner.py \
    --mode micro-live \
    --phase "$PHASE" \
    --bankroll 100.0 \
    --max-pos 10.0 \
    --verbose

echo ""
echo "Micro-live run complete. Check proofs/ for results."
