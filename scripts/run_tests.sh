#!/usr/bin/env bash
# Truth gate — runs the full test suite.
# Exit 0 only on success (fail-closed).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== run_tests.sh ==="
echo "working dir : $(pwd)"
echo "python      : $(python3 --version 2>&1)"
echo ""

# Run pytest with verbose output.
python3 -m pytest tests/ -v --tb=short "$@"

echo ""
echo "=== ALL TESTS PASSED ==="
