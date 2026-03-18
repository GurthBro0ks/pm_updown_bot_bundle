#!/bin/bash
# Run trading bot in shadow mode with monitoring
# Called every 15 minutes by cron

cd /opt/slimy/pm_updown_bot_bundle

# Activate venv and run
source venv/bin/activate 2>/dev/null

# Run in shadow mode for all phases with external timeout wrapper
# If runner hangs for >2 minutes, kill it and exit with code 124 (timeout)
timeout 120 python3 runner.py --mode shadow --phase all --bankroll 100.0 --max-pos 10.0

exit $?
