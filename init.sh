#!/usr/bin/env bash
# PM UpDown Bot Bundle — Agent Environment Init
# Run this at the start of every agent session: source init.sh
set -euo pipefail

echo "=== PM Bot Bundle Init ==="

# 1. Confirm we're in the right directory
if [ ! -f "runner.py" ]; then
  echo "ERROR: Not in pm_updown_bot_bundle root. Run 'cd' to the repo root first."
  exit 1
fi

# 2. Check Python
echo "[1/4] Checking Python environment..."
python3 --version || { echo "ERROR: Python3 not found"; exit 1; }

# 3. Check dependencies
echo "[2/4] Checking dependencies..."
if [ -f "requirements.txt" ]; then
  pip3 install -r requirements.txt -q 2>/dev/null || echo "WARN: Some pip installs failed"
else
  echo "No requirements.txt found — skipping pip install"
fi

# 4. Verify core imports
echo "[3/4] Verifying core imports..."
python3 -c "import runner" 2>/dev/null && echo "runner.py: OK" || echo "WARN: runner.py has import errors"

# 5. Run truth gate (quick check)
echo "[4/4] Quick truth gate check..."
if [ -f "./scripts/run_tests.sh" ]; then
  chmod +x ./scripts/run_tests.sh
  ./scripts/run_tests.sh 2>/dev/null && echo "Truth gate: PASS" || echo "WARN: Truth gate FAILING — fix before new work"
else
  echo "WARN: ./scripts/run_tests.sh not found"
fi

echo ""
echo "=== Init complete. Read claude-progress.md and feature_list.json next. ==="
