#!/usr/bin/env bash
# =============================================================================
# Autoresearch Overnight Launch — Pre-Flight Checklist
# =============================================================================
# Run this before starting the overnight autoresearch loop.
# Aborts with clear errors if any check fails.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BOT_ROOT"

PASS=0
FAIL=0

echo "========================================================================"
echo "  AUTORESEARCH PRE-FLIGHT CHECKLIST"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "========================================================================"
echo ""

# ── 1. Baseline Score ───────────────────────────────────────────────────────
echo "[1] Baseline composite score..."
if [ -f "$BOT_ROOT/proofs" ] || [ -d "$BOT_ROOT/proofs" ]; then
    # Get current score (fast, uses existing proofs)
    SCORE=$(python3 "$SCRIPT_DIR/autoresearch_scorer.py" --proof-dir proofs/ 2>/dev/null | grep "^SCORE:" | sed 's/SCORE: //' || echo "ERROR")
    if [ "$SCORE" != "ERROR" ] && [ -n "$SCORE" ]; then
        echo "      ✓ Baseline score: $SCORE"
        PASS=$((PASS+1))
    else
        echo "      ✗ Could not compute baseline score"
        FAIL=$((FAIL+1))
    fi
else
    echo "      ✗ proofs/ directory not found"
    FAIL=$((FAIL+1))
fi
echo ""

# ── 2. Holdout Set Status ──────────────────────────────────────────────────
echo "[2] Holdout set status..."
if [ -f "$BOT_ROOT/data/holdout/MANIFEST.json" ]; then
    DAYS=$(python3 -c "import json; m=json.load(open('$BOT_ROOT/data/holdout/MANIFEST.json')); print(m.get('days_available','?'))" 2>/dev/null || echo "?")
    FILES=$(python3 -c "import json; m=json.load(open('$BOT_ROOT/data/holdout/MANIFEST.json')); print(m.get('files_copied','?'))" 2>/dev/null || echo "?")
    RANGE=$(python3 -c "import json; m=json.load(open('$BOT_ROOT/data/holdout/MANIFEST.json')); d=m.get('date_range',{}); print(f\"{d.get('oldest','')[:10]} to {d.get('newest','')[:10]}\")" 2>/dev/null || echo "?")
    echo "      ✓ Holdout: $FILES files, $DAYS days available ($RANGE)"
    PASS=$((PASS+1))
else
    echo "      ✗ Holdout MANIFEST.json not found — run: python3 $SCRIPT_DIR/create_holdout.py"
    FAIL=$((FAIL+1))
fi
echo ""

# ── 3. Git Status ─────────────────────────────────────────────────────────
echo "[3] Git working tree..."
GIT_STATUS=$(git status --porcelain 2>/dev/null)
if [ -z "$GIT_STATUS" ]; then
    echo "      ✓ Clean working tree"
    PASS=$((PASS+1))
else
    echo "      ✗ Uncommitted changes — commit or stash before overnight run:"
    echo "$GIT_STATUS" | sed 's/^/         /'
    FAIL=$((FAIL+1))
fi
echo ""

# ── 4. Disk Space ─────────────────────────────────────────────────────────
echo "[4] Disk space (data/holdout/)..."
AVAIL=$(df -BG "$BOT_ROOT/data" 2>/dev/null | awk 'NR==2 {print $4}' | tr -d 'G' || echo "?")
if [ "$AVAIL" != "?" ] && [ "$AVAIL" -gt 1 ]; then
    echo "      ✓ ${AVAIL}GB available"
    PASS=$((PASS+1))
elif [ "$AVAIL" != "?" ]; then
    echo "      ✗ Less than 1GB available (${AVAIL}GB)"
    FAIL=$((FAIL+1))
else
    echo "      ? Could not determine disk space"
    FAIL=$((FAIL+1))
fi
echo ""

# ── 5. API Server ────────────────────────────────────────────────────────
echo "[5] API server (port 8510)..."
if curl -s --max-time 3 http://localhost:8510/api/signals/current > /dev/null 2>&1; then
    echo "      ✓ API server responding"
    PASS=$((PASS+1))
else
    echo "      ✗ API server not responding on port 8510"
    FAIL=$((FAIL+1))
fi
echo ""

# ── 6. Autoresearch Skill ─────────────────────────────────────────────────
echo "[6] Autoresearch skill installed..."
if [ -f "$BOT_ROOT/.claude/skills/autoresearch/SKILL.md" ]; then
    echo "      ✓ Skill found at .claude/skills/autoresearch/SKILL.md"
    PASS=$((PASS+1))
else
    echo "      ✗ Autoresearch skill not found"
    FAIL=$((FAIL+1))
fi
echo ""

# ── 7. program.md ─────────────────────────────────────────────────────────
echo "[7] program.md present..."
if [ -f "$BOT_ROOT/program.md" ]; then
    echo "      ✓ program.md found"
    PASS=$((PASS+1))
else
    echo "      ✗ program.md not found"
    FAIL=$((FAIL+1))
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────
echo "========================================================================"
echo "  SUMMARY: $PASS passed, $FAIL failed"
echo "========================================================================"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "  ✗ ABORTED — fix $FAIL issue(s) above before starting overnight run"
    exit 1
fi

echo ""
echo "  ✓ All checks passed."
echo ""
echo "  To start overnight run:"
echo "    screen -S autoresearch"
echo "    cd $BOT_ROOT"
echo "    # Launch Claude Code and run: /autoresearch"
echo ""
echo "  Morning review:"
echo "    python3 $SCRIPT_DIR/experiment_log.py --summary"
echo "    python3 $SCRIPT_DIR/autoresearch_scorer.py --proof-dir data/holdout/ --verbose"
echo "    git log --oneline -20"
echo ""
