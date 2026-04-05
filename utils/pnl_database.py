"""
SQLite PnL Tracking Database

Provides unified PnL tracking with equity curves and Sharpe ratio calculation.
DB path: /opt/slimy/pm_updown_bot_bundle/paper_trading/pnl.db
"""

import sqlite3
import os
import math
from datetime import datetime, timezone
from typing import Optional

# Database path - configurable via env var
DB_PATH = os.environ.get("PNL_DB_PATH", "/opt/slimy/pm_updown_bot_bundle/paper_trading/pnl.db")


def get_db() -> sqlite3.Connection:
    """Get database connection. Creates DB and tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_db()
    try:
        # Trades table - records buy/sell/exit trades with PnL
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,  -- BUY, EXIT
                price REAL NOT NULL,
                size_usd REAL NOT NULL,
                pnl_usd REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Equity snapshots - portfolio state over time
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cash REAL NOT NULL,
                position_value REAL NOT NULL,
                total_value REAL NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Signals table - trading signals
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase TEXT NOT NULL,
                ticker TEXT NOT NULL,
                signal_type TEXT NOT NULL,  -- buy, sell, exit
                reason TEXT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Create indexes for common queries
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_phase ON trades(phase)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_timestamp ON equity_snapshots(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_phase ON signals(phase)")

        conn.commit()
    finally:
        conn.close()


def record_trade(phase: str, ticker: str, action: str, price: float, size_usd: float,
                  pnl_usd: float = 0, pnl_pct: float = 0):
    """Record a trade (buy or exit) with PnL."""
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO trades (phase, ticker, action, price, size_usd, pnl_usd, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (phase, ticker, action, price, size_usd, pnl_usd, pnl_pct))
        conn.commit()
    except Exception as e:
        print(f"[PNL_DB_ERROR] Failed to record trade: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def record_signal(phase: str, ticker: str, signal_type: str, reason: str = None):
    """Record a trading signal."""
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO signals (phase, ticker, signal_type, reason)
            VALUES (?, ?, ?, ?)
        """, (phase, ticker, signal_type, reason))
        conn.commit()
    except Exception as e:
        print(f"[PNL_DB_ERROR] Failed to record signal: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def snapshot_equity(cash: float, position_value: float, positions: list = None):
    """Record portfolio equity snapshot.

    Args:
        cash: Current cash balance
        position_value: Current value of all positions
        positions: List of position dicts (optional, for future use)
    """
    total_value = cash + position_value
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO equity_snapshots (cash, position_value, total_value)
            VALUES (?, ?, ?)
        """, (cash, position_value, total_value))
        conn.commit()
    except Exception as e:
        print(f"[PNL_DB_ERROR] Failed to snapshot equity: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def get_equity_curve(limit: int = 100) -> list:
    """Get equity curve history.

    Returns:
        List of dicts with timestamp and total_value
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT timestamp, total_value
            FROM equity_snapshots
            ORDER BY timestamp ASC
            LIMIT ?
        """, (limit,)).fetchall()

        return [{"timestamp": r["timestamp"], "total_value": r["total_value"]} for r in rows]
    finally:
        conn.close()


def get_sharpe_ratio(periods_per_day: int = 1) -> Optional[float]:
    """Calculate annualized Sharpe ratio from equity curve.

    Args:
        periods_per_day: Number of equity snapshots per day (default 1)

    Returns:
        Annualized Sharpe ratio, or None if insufficient data (< 2 points)
    """
    curve = get_equity_curve(limit=1000)
    if len(curve) < 2:
        return None

    # Calculate returns
    returns = []
    for i in range(1, len(curve)):
        prev_val = curve[i-1]["total_value"]
        curr_val = curve[i]["total_value"]
        if prev_val > 0:
            ret = (curr_val - prev_val) / prev_val
            returns.append(ret)

    if not returns:
        return None

    # Calculate mean and std dev
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        return None

    # Annualize (assuming periods_per_day snapshots)
    # Daily return mean * sqrt(252) for annualization
    periods_per_year = periods_per_day * 252
    sharpe = (mean_return / std_dev) * math.sqrt(periods_per_year)

    return round(sharpe, 3)


def get_phase_performance() -> list:
    """Get PnL breakdown by phase.

    Returns:
        List of dicts with phase stats
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                phase,
                COUNT(*) as trade_count,
                SUM(CASE WHEN action = 'EXIT' THEN 1 ELSE 0 END) as exit_count,
                SUM(CASE WHEN action = 'EXIT' THEN pnl_usd ELSE 0 END) as total_pnl,
                AVG(CASE WHEN action = 'EXIT' THEN pnl_pct ELSE NULL END) as avg_pnl_pct,
                MIN(timestamp) as first_trade,
                MAX(timestamp) as last_trade
            FROM trades
            GROUP BY phase
            ORDER BY phase
        """).fetchall()

        return [
            {
                "phase": r["phase"],
                "trade_count": r["trade_count"],
                "exit_count": r["exit_count"],
                "total_pnl": round(r["total_pnl"] or 0, 2),
                "avg_pnl_pct": round(r["avg_pnl_pct"] or 0, 2),
                "first_trade": r["first_trade"],
                "last_trade": r["last_trade"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# Initialize on import
init_db()
