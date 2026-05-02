"""
Kalshi Orderbook Snapshot SQLite Storage Layer

Minimal storage using a separate database file from pnl.db.
Location: paper_trading/orderbook_snapshots.db
"""

import json
import logging
import os
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "paper_trading", "orderbook_snapshots.db"
)

INIT_SQL = """
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT 'kalshi',
    ticker TEXT NOT NULL,
    question TEXT,
    yes_bid REAL,
    yes_ask REAL,
    spread_cents REAL,
    volume_24h REAL,
    open_interest REAL,
    orderbook_json TEXT,
    recent_trades_json TEXT,
    events_json TEXT,
    event_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_ts
    ON orderbook_snapshots(ticker, timestamp_utc);

CREATE INDEX IF NOT EXISTS idx_snapshots_events
    ON orderbook_snapshots(event_count) WHERE event_count > 0;
"""


def _init_db(db_path: str) -> None:
    """Initialize the database schema if not exists."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(INIT_SQL)
        conn.commit()
    finally:
        conn.close()


def store_snapshot(db_path: Optional[str] = None, snapshot: dict = None) -> int:
    """Store a single snapshot and return the row id.

    Args:
        db_path: Path to SQLite database (uses default if None)
        snapshot: Snapshot dict from orderbook_signal.collect_snapshot()

    Returns:
        Row id (integer)
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    _init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO orderbook_snapshots
            (timestamp_utc, venue, ticker, question, yes_bid, yes_ask,
             spread_cents, volume_24h, open_interest,
             orderbook_json, recent_trades_json, events_json, event_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.get("timestamp_utc", ""),
                snapshot.get("venue", "kalshi"),
                snapshot.get("ticker", ""),
                snapshot.get("question", ""),
                snapshot.get("yes_bid"),
                snapshot.get("yes_ask"),
                snapshot.get("spread_cents"),
                snapshot.get("volume_24h"),
                snapshot.get("open_interest"),
                json.dumps(snapshot.get("orderbook", {})),
                json.dumps(snapshot.get("recent_trades", [])),
                json.dumps(snapshot.get("events", [])),
                len(snapshot.get("events", [])),
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        logger.info(f"[ORDERBOOK_STORE] Stored snapshot id={row_id} ticker={snapshot.get('ticker')}")
        return row_id
    finally:
        conn.close()


def get_snapshots(
    db_path: Optional[str] = None,
    ticker: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> list:
    """Retrieve snapshots from the database.

    Args:
        db_path: Path to SQLite database (uses default if None)
        ticker: Filter by ticker (optional)
        since: ISO datetime string, filter timestamp_utc >= since (optional)
        limit: Maximum rows to return

    Returns:
        List of snapshot dicts
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        params = []
        conditions = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)

        if since:
            conditions.append("timestamp_utc >= ?")
            params.append(since)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT * FROM orderbook_snapshots
            {where_clause}
            ORDER BY timestamp_utc DESC
            LIMIT ?
        """
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            # Rehydrate JSON columns
            for col in ("orderbook_json", "recent_trades_json", "events_json"):
                try:
                    d[col.replace("_json", "")] = json.loads(d[col]) if d[col] else {}
                except json.JSONDecodeError:
                    d[col.replace("_json", "")] = {}
                del d[col]
            results.append(d)

        return results
    finally:
        conn.close()


def get_event_snapshots(
    db_path: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> list:
    """Retrieve only snapshots that have detected events.

    Args:
        db_path: Path to SQLite database (uses default if None)
        since: ISO datetime string, filter timestamp_utc >= since (optional)
        limit: Maximum rows to return

    Returns:
        List of snapshot dicts with events
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        params = []
        conditions = ["event_count > 0"]

        if since:
            conditions.append("timestamp_utc >= ?")
            params.append(since)

        where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT * FROM orderbook_snapshots
            {where_clause}
            ORDER BY timestamp_utc DESC
            LIMIT ?
        """
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            for col in ("orderbook_json", "recent_trades_json", "events_json"):
                try:
                    d[col.replace("_json", "")] = json.loads(d[col]) if d[col] else {}
                except json.JSONDecodeError:
                    d[col.replace("_json", "")] = {}
                del d[col]
            results.append(d)

        return results
    finally:
        conn.close()
