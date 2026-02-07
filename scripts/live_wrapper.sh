#!/bin/bash
set -euo pipefail
cd /opt/slimy/pm_updown_bot_bundle
source .env
exec python3 runner.py "$@"