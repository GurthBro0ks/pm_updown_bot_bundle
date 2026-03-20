#!/bin/bash
# Autoresearch Single-Run Scorer
# Runs one complete shadow cycle and outputs composite score.
# Format: "SCORE: {composite_score:.4f}"
# This becomes the "verify" command for the autoresearch loop.

set -euo pipefail

BOT_DIR="/opt/slimy/pm_updown_bot_bundle"
PROOF_DIR="$BOT_DIR/proofs"
SCRIPT_DIR="$BOT_DIR/scripts"

echo "=== AUTORESEARCH RUN ==="
echo "Started: $(date)"
echo ""

# Run one shadow cycle
cd "$BOT_DIR"
echo "[1/2] Running shadow cycle..."
python3 runner.py --mode shadow --phase all --bankroll 100 --max-pos 10 2>&1 | tail -20

echo ""
echo "[2/2] Computing composite score..."
SCORE=$(python3 "$SCRIPT_DIR/autoresearch_scorer.py" --proof-dir "$PROOF_DIR" 2>/dev/null | grep "^SCORE:" | sed 's/SCORE: //')

if [ -z "$SCORE" ]; then
    echo "ERROR: Failed to compute score"
    exit 1
fi

echo ""
echo "SCORE: $SCORE"
echo "Completed: $(date)"
