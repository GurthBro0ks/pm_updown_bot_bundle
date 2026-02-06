#!/usr/bin/env bash
# Convenience wrapper at repo root — delegates to scripts/run_tests.sh
set -euo pipefail
exec bash scripts/run_tests.sh "$@"
