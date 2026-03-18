#!/usr/bin/env python3
"""
Paper Trading PnL Tracker

Logs every shadow signal with:
- Ticker, sentiment score, entry price, timestamp
- Checks price 1h/4h/1d later
- Calculates hypothetical PnL

After 2 weeks of data, you'll know which signals are actually profitable
before risking real money.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List
import requests

# Setup
DB_PATH = Path("/opt/slimy/pm_updown_bot_bundle/paper_trading/paper_pnl.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Massive API for price checks
MASSIVE_API_KEY = os.getenv('MASSIVE_API_KEY')


def init_db():
    """Initialize paper trading database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Signals table
    c.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            sentiment REAL NOT NULL,
            entry_price REAL NOT NULL,
            position_size REAL NOT NULL,
            signal_time TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # PnL checks table
    c.execute('''
        CREATE TABLE IF NOT EXISTS pnl_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            check_time TEXT NOT NULL,
            current_price REAL NOT NULL,
            pnl_usd REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            hours_elapsed REAL NOT NULL,
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        )
    ''')
    
    conn.commit()
    conn.close()


def log_signal(ticker: str, sentiment: float, entry_price: float, position_size: float) -> int:
    """
    Log a paper trading signal
    
    Returns signal ID
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO signals (ticker, sentiment, entry_price, position_size, signal_time)
        VALUES (?, ?, ?, ?, ?)
    ''', (ticker, sentiment, entry_price, position_size, datetime.now(timezone.utc).isoformat()))
    
    signal_id = c.lastrowid
    conn.commit()
    conn.close()
    
    return signal_id


def fetch_current_price(ticker: str) -> Optional[float]:
    """Fetch current price from Massive API"""
    if not MASSIVE_API_KEY:
        return None
    
    try:
        url = f"https://api.massive.com/v2/aggs/ticker/{ticker}/prev?apiKey={MASSIVE_API_KEY}"
        resp = requests.get(url, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            if data.get("results"):
                return data["results"][0].get("c")  # Close price
        return None
    except Exception as e:
        print(f"Price fetch error for {ticker}: {e}")
        return None


def check_pnl():
    """
    Check PnL for all signals at 1h, 4h, 1d intervals
    Called periodically (e.g., every hour)
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get all signals that haven't been checked in the last hour
    c.execute('''
        SELECT id, ticker, entry_price, position_size, signal_time
        FROM signals
        WHERE id NOT IN (
            SELECT DISTINCT signal_id FROM pnl_checks 
            WHERE check_time > datetime('now', '-1 hour')
        )
    ''')
    
    signals = c.fetchall()
    
    for signal_id, ticker, entry_price, position_size, signal_time in signals:
        # Calculate hours elapsed
        signal_dt = datetime.fromisoformat(signal_time.replace('Z', '+00:00'))
        elapsed = datetime.now(timezone.utc) - signal_dt
        hours_elapsed = elapsed.total_seconds() / 3600
        
        # Only check at 1h, 4h, 24h intervals
        check_points = [1, 4, 24]
        should_check = any(
            abs(hours_elapsed - cp) < 0.5  # Within 30 min of check point
            for cp in check_points
        )
        
        if not should_check:
            continue
        
        # Get current price
        current_price = fetch_current_price(ticker)
        if not current_price:
            continue
        
        # Calculate PnL
        price_change = current_price - entry_price
        pnl_usd = (price_change / entry_price) * position_size
        pnl_pct = (price_change / entry_price) * 100
        
        # Log check
        c.execute('''
            INSERT INTO pnl_checks (signal_id, check_time, current_price, pnl_usd, pnl_pct, hours_elapsed)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (signal_id, datetime.now(timezone.utc).isoformat(), current_price, pnl_usd, pnl_pct, hours_elapsed))
        
        print(f"PnL check: {ticker} @ {hours_elapsed:.1f}h - ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)")
    
    conn.commit()
    conn.close()


def get_performance_report(days: int = 14) -> Dict:
    """
    Get performance report for the last N days
    
    Returns statistics on signal quality
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get all signals with their PnL checks
    c.execute('''
        SELECT 
            s.ticker,
            s.sentiment,
            s.entry_price,
            s.position_size,
            s.signal_time,
            pc.current_price,
            pc.pnl_usd,
            pc.pnl_pct,
            pc.hours_elapsed
        FROM signals s
        LEFT JOIN pnl_checks pc ON s.id = pc.signal_id
        WHERE s.signal_time > datetime('now', ?)
        ORDER BY s.signal_time DESC
    ''', (f'-{days} days',))
    
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"total_signals": 0, "message": "No data yet"}
    
    # Aggregate stats
    total_signals = len(set(r[0] for r in rows if r[0]))
    
    # 1h PnL
    checks_1h = [r for r in rows if r[8] and abs(r[8] - 1) < 0.5]
    avg_1h_pnl = sum(r[7] for r in checks_1h if r[7]) / len(checks_1h) if checks_1h else 0
    
    # 4h PnL
    checks_4h = [r for r in rows if r[8] and abs(r[8] - 4) < 0.5]
    avg_4h_pnl = sum(r[7] for r in checks_4h if r[7]) / len(checks_4h) if checks_4h else 0
    
    # 24h PnL
    checks_24h = [r for r in rows if r[8] and abs(r[8] - 24) < 0.5]
    avg_24h_pnl = sum(r[7] for r in checks_24h if r[7]) / len(checks_24h) if checks_24h else 0
    
    # Win rate (positive 24h PnL)
    wins = len([r for r in checks_24h if r[7] and r[7] > 0])
    total_24h = len(checks_24h)
    win_rate = (wins / total_24h * 100) if total_24h > 0 else 0
    
    return {
        "total_signals": total_signals,
        "period_days": days,
        "1h_avg_pnl": f"${avg_1h_pnl:+.2f}",
        "4h_avg_pnl": f"${avg_4h_pnl:+.2f}",
        "24h_avg_pnl": f"${avg_24h_pnl:+.2f}",
        "24h_win_rate": f"{win_rate:.1f}%",
        "total_24h_checks": total_24h,
        "recommendation": "WAIT" if total_24h < 20 else ("PROFITABLE" if avg_24h_pnl > 0 else "UNPROFITABLE")
    }


if __name__ == "__main__":
    init_db()
    print("Paper trading PnL tracker initialized")
    
    # Example usage
    report = get_performance_report()
    print(json.dumps(report, indent=2))
