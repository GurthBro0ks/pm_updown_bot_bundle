#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Load env
set -a
source ".ralph/ralph.env"
set +a

TS="$(date -u +%Y%m%dT%H%M%SZ)"
PROOF_DIR="${PROOF_DIR:-/tmp/proof_ralph_pm_shadow_${TS}}"
mkdir -p "$PROOF_DIR"

echo "[META] PROOF_DIR=$PROOF_DIR" | tee "$PROOF_DIR/00_meta.txt"
echo "[META] HEAD=$(git rev-parse --short HEAD)" | tee -a "$PROOF_DIR/00_meta.txt"
echo "[META] BRANCH=$(git rev-parse --abbrev-ref HEAD)" | tee -a "$PROOF_DIR/00_meta.txt"

# Require clean tree (fail-closed)
if [[ -n "$(git status --porcelain)" ]]; then
  echo "[FAIL] Working tree not clean. Commit/stash first." | tee -a "$PROOF_DIR/00_meta.txt"
  exit 2
fi

# Ensure beads initialized (stealth)
if [[ "${BEADS_STEALTH:-0}" == "1" ]]; then
  bd init --stealth >/dev/null 2>&1 || true
else
  bd init >/dev/null 2>&1 || true
fi

# Create batch branch
BRANCH="${BRANCH_PREFIX}/${BATCH_SLUG}-${TS}"
git checkout -b "$BRANCH"
echo "[META] NEW_BRANCH=$BRANCH" | tee -a "$PROOF_DIR/00_meta.txt"

# Capture versions (proof)
{
  echo "date_utc=$(date -u -Is)"
  echo "git=$(git --version)"
  echo "python=$(python3 --version 2>&1 || true)"
  echo "node=$(node --version 2>&1 || true)"
  echo "pnpm=$(pnpm --version 2>&1 || true)"
  echo "bd=$(bd --version 2>&1 || true)"
  echo "claude=$(claude --version 2>&1 || true)"
} | tee "$PROOF_DIR/01_versions.txt"

touch "$PROOF_DIR/last_run_log.txt"

for i in $(seq 1 "${MAX_ROUNDS}"); do
  ROUND_DIR="$PROOF_DIR/round_$(printf "%02d" "$i")"
  mkdir -p "$ROUND_DIR"
  echo "=== ROUND $i ===" | tee "$ROUND_DIR/round.txt"

  # Snapshot next ready beads issue into tasks.json
  set +e
  BEADS_ID="$(python3 scripts/ralph/beads_snapshot.py 2>"$ROUND_DIR/beads_snapshot_err.txt")"
  SNAP_RC=$?
  set -e
  echo "SNAP_RC=$SNAP_RC" | tee "$ROUND_DIR/beads_snapshot_rc.txt"
  if [[ "$SNAP_RC" -ne 0 ]]; then
    echo "[FAIL] No ready beads work or snapshot failed." | tee "$ROUND_DIR/fail.txt"
    exit 3
  fi
  echo "BEADS_ID=$BEADS_ID" | tee "$ROUND_DIR/beads_id.txt"

  # Mark in_progress (best-effort)
  bd update "$BEADS_ID" --status in_progress --json >"$ROUND_DIR/beads_update.json" 2>"$ROUND_DIR/beads_update_err.txt" || true

  LAST_LOG="$(cat "$PROOF_DIR/last_run_log.txt" 2>/dev/null || true)"

  cat > "$ROUND_DIR/prompt.txt" <<PROMPT
$(cat .ralph/RALPH_PROMPT.md)

GOAL (north star):
- Polymarket bot shadow-mode runner + artifacts + tests.

CURRENT TASK (from tasks.json):
$(cat tasks.json)

LAST RUN LOG:
$LAST_LOG

OUTPUT RULES:
- Output ONLY a unified diff patch suitable for: git apply
- No markdown. No commentary.
PROMPT

  # Agent generates patch
  set +e
  bash -lc "${AGENT_CMD} < \"$ROUND_DIR/prompt.txt\" > \"$ROUND_DIR/patch.diff\" 2> \"$ROUND_DIR/agent_stderr.txt\""
  AGENT_RC=$?
  set -e
  echo "AGENT_RC=$AGENT_RC" | tee "$ROUND_DIR/agent_rc.txt"

  if [[ ! -s "$ROUND_DIR/patch.diff" ]]; then
    echo "[FAIL] Empty patch from agent." | tee "$ROUND_DIR/fail.txt"
    printf "Empty patch.\n" > "$PROOF_DIR/last_run_log.txt"
    continue
  fi

  # Apply patch
  set +e
  git apply --whitespace=nowarn "$ROUND_DIR/patch.diff" >"$ROUND_DIR/apply_out.txt" 2>"$ROUND_DIR/apply_err.txt"
  APPLY_RC=$?
  set -e
  echo "APPLY_RC=$APPLY_RC" | tee "$ROUND_DIR/apply_rc.txt"
  if [[ "$APPLY_RC" -ne 0 ]]; then
    echo "[FAIL] Patch did not apply." | tee "$ROUND_DIR/fail.txt"
    cat "$ROUND_DIR/apply_err.txt" > "$PROOF_DIR/last_run_log.txt"
    continue
  fi

  # Forbidden guard (fail-closed)
  set +e
  python3 scripts/ralph/guard_forbidden.py >"$ROUND_DIR/forbidden_out.txt" 2>"$ROUND_DIR/forbidden_err.txt"
  FORB_RC=$?
  set -e
  echo "FORB_RC=$FORB_RC" | tee "$ROUND_DIR/forbidden_rc.txt"
  if [[ "$FORB_RC" -ne 0 ]]; then
    echo "[FAIL] Forbidden paths touched. Reverting patch." | tee "$ROUND_DIR/fail.txt"
    git reset --hard -q
    cat "$ROUND_DIR/forbidden_out.txt" > "$PROOF_DIR/last_run_log.txt"
    continue
  fi

  # Run gate
  set +e
  bash scripts/ralph/gate.sh >"$ROUND_DIR/gate_out.txt" 2>"$ROUND_DIR/gate_err.txt"
  GATE_RC=$?
  set -e
  echo "GATE_RC=$GATE_RC" | tee "$ROUND_DIR/gate_rc.txt"

  {
    echo "Gate RC: $GATE_RC"
    echo "--- gate_out ---"
    cat "$ROUND_DIR/gate_out.txt"
    echo "--- gate_err ---"
    cat "$ROUND_DIR/gate_err.txt"
  } > "$PROOF_DIR/last_run_log.txt"

  if [[ "$GATE_RC" -eq 0 ]]; then
    echo "[PASS] Gate passed." | tee "$ROUND_DIR/pass.txt"

    # Auto-create report + buglog to satisfy Definition of Done
    mkdir -p docs/buglog

    BUG_TS="$(date -u +%Y-%m-%d)"
    BUG_FILE="docs/buglog/BUG_${BUG_TS}_ralph_${BEADS_ID}.md"

    cat > "$BUG_FILE" <<EOF
# Ralph Closeout — ${BEADS_ID}

- UTC: $(date -u -Is)
- Branch: ${BRANCH}
- Repo: $(pwd)
- Task: $(python3 -c "import json;print(json.load(open('tasks.json'))['current']['title'])")
- Gate: ${GATE_CMD}

## What changed (git diff summary)
\`\`\`
$(git diff --stat)
\`\`\`

## Gate output (tail)
\`\`\`
$(tail -n 80 "$ROUND_DIR/gate_out.txt" 2>/dev/null || true)
\`\`\`
EOF

    cat > "$PROOF_DIR/REPORT.md" <<EOF
# PROOF — Ralph Batch

- Beads: ${BEADS_ID}
- Branch: ${BRANCH}
- Gate: ${GATE_CMD}
- Result: PASS

Artifacts:
- Buglog: ${BUG_FILE}
- Proof dir: ${PROOF_DIR}
EOF

    echo "DONE" > "$PROOF_DIR/DONE.txt"

    # Close bead (best-effort)
    bd close "$BEADS_ID" --reason "Gate PASS + buglog created" --json >"$ROUND_DIR/beads_close.json" 2>"$ROUND_DIR/beads_close_err.txt" || true

    echo "[DONE] Proof: $PROOF_DIR"
    exit 0
  else
    echo "[FAIL] Gate failed; next round will attempt fix." | tee "$ROUND_DIR/fail.txt"
  fi
done

echo "[DONE] Max rounds reached without passing gate."
exit 1

