#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

SESSION="ralph_pm_shadow"
tmux has-session -t "$SESSION" 2>/dev/null && {
  echo "tmux session '$SESSION' already exists."
  echo "Attach: tmux attach -t $SESSION"
  exit 0
}

tmux new-session -d -s "$SESSION" "bash scripts/ralph/loop.sh |& tee /tmp/${SESSION}_latest.log"
echo "Started: tmux attach -t $SESSION"
echo "Tail log: tail -f /tmp/${SESSION}_latest.log"

