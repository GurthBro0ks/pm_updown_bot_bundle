#!/usr/bin/env bash
set -euo pipefail

# Loads .ralph/ralph.env if present
if [[ -f ".ralph/ralph.env" ]]; then
  set -a
  source ".ralph/ralph.env"
  set +a
fi

: "${GATE_CMD:=bash scripts/run_tests.sh}"

echo "[GATE] $(date -Is)"
echo "[GATE] cmd: ${GATE_CMD}"

bash -lc "${GATE_CMD}"

echo "[GATE] PASS"

