#!/bin/bash
# Paper Trading PnL Check
# Runs every hour to track signal performance

cd /opt/slimy/pm_updown_bot_bundle
python3 -c "
from paper_trading.pnl_tracker import check_pnl, get_performance_report
import json

# Check PnL for existing signals
check_pnl()

# Generate report
report = get_performance_report(days=14)
print('=== Paper Trading Performance Report ===')
print(json.dumps(report, indent=2))
" 2>&1 | tee -a /opt/slimy/pm_updown_bot_bundle/logs/paper_pnl.log
