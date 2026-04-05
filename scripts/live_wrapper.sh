#!/bin/bash
set -euo pipefail
cd /opt/slimy/pm_updown_bot_bundle
source .env
exec /usr/bin/timeout 600 python3 runner.py "$@"