#!/usr/bin/env bash

# Daily report to Discord — runs at 05:00 America/Detroit via cron
# Cron line: 0 9 * * * /opt/slimy/pm_updown_bot_bundle/scripts/cron_daily_report.sh
# (09:00 UTC = 05:00 EDT / 04:00 EST — cron uses UTC)

set -euo pipefail

cd /opt/slimy/pm_updown_bot_bundle
source .env 2>/dev/null || true
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

LOG="logs/daily_report_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs

./venv/bin/python3 -c "
import sys, json, sqlite3, os
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from utils.discord_notify import send_daily_report

# Get balance
try:
    from utils.kalshi_orders import KalshiOrderClient
    client = KalshiOrderClient()
    bal_data = client.get_balance()
    balance = bal_data.get('balance', -1) if isinstance(bal_data, dict) else bal_data
    positions = client.get_positions()
except Exception as e:
    print(f'Failed to get live data: {e}')
    balance = -1
    positions = []

# Get 7-day summary from pnl.db
trades_7d = {'total_orders': 0, 'total_fills': 0, 'total_pnl_usd': 0}
try:
    db = sqlite3.connect('paper_trading/pnl.db')
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute('''
        SELECT count(*) as cnt, sum(pnl_usd) as pnl
        FROM trades
        WHERE timestamp >= datetime('now', '-7 days')
    ''')
    row = cur.fetchone()
    if row:
        trades_7d['total_orders'] = row['cnt'] or 0
        trades_7d['total_pnl_usd'] = row['pnl'] or 0
    # Count fills separately if there's a status column
    cur.execute('PRAGMA table_info(trades)')
    cols = {r[1] for r in cur.fetchall()}
    if 'status' in cols:
        cur.execute('''
            SELECT count(*) FROM trades
            WHERE timestamp >= datetime('now', '-7 days')
            AND status = 'filled'
        ''')
        fills = cur.fetchone()
        trades_7d['total_fills'] = fills[0] if fills else 0
    db.close()
except Exception as e:
    print(f'DB query failed: {e}')

# Get last 24h trades
trades_24h = []
try:
    db = sqlite3.connect('paper_trading/pnl.db')
    cur = db.cursor()
    cur.execute('''
        SELECT * FROM trades
        WHERE timestamp >= datetime('now', '-1 day')
    ''')
    trades_24h = cur.fetchall()
    db.close()
except Exception:
    pass

send_daily_report(
    balance_usd=balance,
    open_positions=positions if isinstance(positions, list) else [],
    trades_24h=trades_24h,
    trades_7d_summary=trades_7d,
)
print('Daily report sent to Discord')
" 2>&1 | tee "$LOG"
