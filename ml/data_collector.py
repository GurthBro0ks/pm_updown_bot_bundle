#!/usr/bin/env python3
"""
Data Collector — XGBoost Training Pipeline

Collects market observations and price history for ML training.
Stores data in ml/training_data.db.

Usage:
  python3 -m ml.data_collector           # collect one round of observations
  python3 -m ml.data_collector --stats   # print database statistics
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Resolve imports from bundle
BUNDLE_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
sys.path.insert(0, str(BUNDLE_DIR))

from utils.kalshi import fetch_kalshi_markets, get_kalshi_headers

# Paths
DB_PATH = BUNDLE_DIR / "ml" / "training_data.db"

# Rate limiting
API_DELAY_SEC = 0.05


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    observed_at TEXT NOT NULL,

    -- Market info at observation time
    title TEXT,
    category TEXT,
    yes_price REAL NOT NULL,
    no_price REAL NOT NULL,
    spread REAL,
    volume_24h REAL,
    open_interest REAL,
    liquidity_usd REAL,
    hours_to_close REAL,
    hours_to_open REAL,

    -- Temporal features
    day_of_week INTEGER,
    hour_of_day INTEGER,
    is_weekend INTEGER,

    -- Price dynamics (NULL until enough history)
    price_delta_1h REAL,
    price_delta_6h REAL,
    price_delta_24h REAL,
    volatility_24h REAL,
    momentum REAL,
    mean_reversion_signal REAL,

    -- Category encoding
    category_encoded INTEGER,

    -- Label (NULL until resolved)
    outcome INTEGER,
    settlement_price REAL,

    UNIQUE(ticker, observed_at)
);

CREATE TABLE IF NOT EXISTS market_price_history (
    ticker TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    yes_price REAL NOT NULL,
    no_price REAL NOT NULL,
    volume_24h REAL,
    PRIMARY KEY (ticker, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_obs_ticker ON market_observations(ticker);
CREATE INDEX IF NOT EXISTS idx_obs_labeled ON market_observations(outcome);
CREATE INDEX IF NOT EXISTS idx_history_ticker ON market_price_history(ticker);
"""


def get_db():
    """Get a database connection with WAL mode."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Kalshi API helpers (copied/adapted from utils/kalshi.py)
# ---------------------------------------------------------------------------

KALSHI_API_KEY = os.getenv("KALSHI_KEY", "d1ac170e-5bb1-4d6a-b483-a2f76e072c7a")
KALSHI_SECRET_FILE = os.getenv("KALSHI_SECRET_FILE", str(BUNDLE_DIR / "keys" / "kalshi-prod.key"))
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def _get_kalshi_headers(method: str, path: str) -> dict:
    """Generate Kalshi signed headers."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes
    import base64

    with open(KALSHI_SECRET_FILE, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    timestamp = str(int(time.time() * 1000))
    path_without_query = path.split("?")[0]
    msg = f"{timestamp}{method}/trade-api/v2{path_without_query}"

    signature = private_key.sign(
        msg.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


def get_market_detail(ticker: str) -> dict:
    """Fetch single market details from Kalshi API."""
    import requests

    path = f"/markets/{ticker}"
    headers = _get_kalshi_headers("GET", path)

    try:
        resp = requests.get(f"{KALSHI_BASE_URL}{path}", headers=headers, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        return data.get("market", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Price history helpers
# ---------------------------------------------------------------------------

def get_price_at(ticker: str, observed_at: str, conn: sqlite3.Connection) -> tuple:
    """
    Get price at a specific observation time.
    Returns (yes_price, no_price) or (None, None).
    """
    row = conn.execute(
        "SELECT yes_price, no_price FROM market_price_history WHERE ticker=? AND observed_at=?",
        (ticker, observed_at),
    ).fetchone()
    if row:
        return (row["yes_price"], row["no_price"])
    return (None, None)


def get_recent_prices(
    ticker: str,
    current_at: datetime,
    hours: list[int],
    conn: sqlite3.Connection,
) -> dict:
    """
    Get prices at N hours ago from current observation time.
    Returns {1: price_1h_ago, 6: price_6h_ago, 24: price_24h_ago}.
    """
    result = {}
    for h in hours:
        target = current_at - timedelta(hours=h)
        target_str = target.strftime("%Y-%m-%d %H:%M:%S")

        # Find closest price history entry at or before target
        row = conn.execute(
            """SELECT yes_price FROM market_price_history
               WHERE ticker=? AND observed_at <= ?
               ORDER BY observed_at DESC LIMIT 1""",
            (ticker, target_str),
        ).fetchone()

        result[h] = row["yes_price"] if row else None

    return result


def compute_volatility(conn: sqlite3.Connection, ticker: str, current_at: datetime) -> float | None:
    """
    Compute 24h price volatility (std dev of price changes).
    """
    cutoff = (current_at - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT yes_price FROM market_price_history
           WHERE ticker=? AND observed_at >= ?
           ORDER BY observed_at ASC""",
        (ticker, cutoff),
    ).fetchall()

    if len(rows) < 2:
        return None

    prices = [r["yes_price"] for r in rows]
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    mean_change = sum(changes) / len(changes)
    variance = sum((c - mean_change) ** 2 for c in changes) / len(changes)
    return math.sqrt(variance)


def compute_24h_avg_price(conn: sqlite3.Connection, ticker: str, current_at: datetime) -> float | None:
    """Get average price over the past 24 hours."""
    cutoff = (current_at - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT yes_price FROM market_price_history
           WHERE ticker=? AND observed_at >= ?
           ORDER BY observed_at ASC""",
        (ticker, cutoff),
    ).fetchall()

    if not rows:
        return None
    return sum(r["yes_price"] for r in rows) / len(rows)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_market_observations(verbose: bool = False) -> int:
    """
    Fetch active Kalshi markets and record observations.
    Returns number of new observations inserted.
    """
    import requests

    print("[INFO] Fetching active Kalshi markets...", file=sys.stderr)
    markets = fetch_kalshi_markets()

    if not markets:
        print("[WARN] No markets returned from Kalshi API.", file=sys.stderr)
        return 0

    print(f"[INFO] Got {len(markets)} markets", file=sys.stderr)

    conn = get_db()
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0

    for m in markets:
        ticker = m.get("id", "")
        if not ticker:
            continue

        yes_price = m.get("odds", {}).get("yes", 0)
        no_price = m.get("odds", {}).get("no", 1.0 - yes_price)
        if yes_price <= 0:
            continue

        spread = abs(m.get("yes_ask", 0) - m.get("yes_bid", 0)) / 100.0 if m.get("yes_ask") else None
        volume_24h = m.get("volume_24h", 0) or 0
        open_interest = m.get("open_interest", 0) or 0
        liquidity_usd = m.get("liquidity_usd", 0) or 0
        hours_to_close = m.get("hours_to_end", 48)
        hours_to_open = m.get("hours_to_start", 0)

        category = m.get("series_category", "unknown")
        title = m.get("question", ticker)

        # Temporal features
        day_of_week = now.weekday()
        hour_of_day = now.hour
        is_weekend = 1 if day_of_week >= 5 else 0

        # Category encoding
        CATEGORY_CODES = {
            "economics": 0, "finance": 1, "politics": 2, "elections": 3,
            "companies": 4, "climate": 5, "weather": 6, "world": 7,
            "crypto": 8, "science": 9, "technology": 10, "sports": 11,
            "esports": 12, "entertainment": 13, "social": 14, "other": 15,
        }
        category_encoded = CATEGORY_CODES.get(category.lower(), 15)

        # Price dynamics (requires history)
        recent = get_recent_prices(ticker, now, [1, 6, 24], conn)
        price_1h_ago = recent.get(1)
        price_6h_ago = recent.get(6)
        price_24h_ago = recent.get(24)

        price_delta_1h = (yes_price - price_1h_ago) if price_1h_ago is not None else None
        price_delta_6h = (yes_price - price_6h_ago) if price_6h_ago is not None else None
        price_delta_24h = (yes_price - price_24h_ago) if price_24h_ago is not None else None

        volatility_24h = compute_volatility(conn, ticker, now)
        price_24h_avg = compute_24h_avg_price(conn, ticker, now)

        # Momentum: weighted 1h (2x) + normalized 6h
        momentum = None
        if price_delta_1h is not None and price_delta_6h is not None:
            momentum = (2 * price_delta_1h) + (price_delta_6h / 6)
        elif price_delta_6h is not None:
            momentum = price_delta_6h / 6
        elif price_delta_1h is not None:
            momentum = price_delta_1h

        # Mean reversion signal
        mean_reversion_signal = (yes_price - price_24h_avg) if price_24h_avg is not None else None

        # Upsert observation
        try:
            conn.execute(
                """INSERT OR REPLACE INTO market_observations
                   (ticker, observed_at, title, category, yes_price, no_price, spread,
                    volume_24h, open_interest, liquidity_usd, hours_to_close, hours_to_open,
                    day_of_week, hour_of_day, is_weekend,
                    price_delta_1h, price_delta_6h, price_delta_24h,
                    volatility_24h, momentum, mean_reversion_signal, category_encoded)
                   VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker, now_str, title, category, yes_price, no_price, spread,
                    volume_24h, open_interest, liquidity_usd, hours_to_close, hours_to_open,
                    day_of_week, hour_of_day, is_weekend,
                    price_delta_1h, price_delta_6h, price_delta_24h,
                    volatility_24h, momentum, mean_reversion_signal, category_encoded,
                ),
            )
            inserted += 1
        except Exception as e:
            if verbose:
                print(f"[WARN] Failed to insert observation for {ticker}: {e}", file=sys.stderr)

        # Upsert price history
        try:
            conn.execute(
                """INSERT OR REPLACE INTO market_price_history
                   (ticker, observed_at, yes_price, no_price, volume_24h)
                   VALUES (?, ?, ?, ?, ?)""",
                (ticker, now_str, yes_price, no_price, volume_24h),
            )
        except Exception:
            pass

        if verbose and inserted % 20 == 0:
            print(f"[DEBUG] Processed {inserted}/{len(markets)} markets...", file=sys.stderr)

        time.sleep(API_DELAY_SEC)

    conn.commit()
    conn.close()

    print(f"[INFO] Inserted {inserted} market observations", file=sys.stderr)
    return inserted


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def print_stats():
    """Print database statistics."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) as c FROM market_observations").fetchone()["c"]
    labeled = conn.execute("SELECT COUNT(*) as c FROM market_observations WHERE outcome IS NOT NULL").fetchone()["c"]
    unlabeled = total - labeled

    print(f"Total observations:  {total}")
    print(f"Labeled:              {labeled}")
    print(f"Unlabeled:            {unlabeled}")

    # Category breakdown
    print("\nCategory breakdown:")
    rows = conn.execute(
        """SELECT category, COUNT(*) as c, SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) as labeled
           FROM market_observations GROUP BY category ORDER BY c DESC"""
    ).fetchall()
    for r in rows:
        print(f"  {r['category'] or 'unknown':20s}  total={r['c']:5d}  labeled={r['labeled']}")

    # Ticker with most observations
    top_ticker = conn.execute(
        "SELECT ticker, COUNT(*) as c FROM market_observations GROUP BY ticker ORDER BY c DESC LIMIT 1"
    ).fetchone()
    if top_ticker:
        print(f"\nMost observed ticker:  {top_ticker['ticker']} ({top_ticker['c']} observations)")

    # Days of coverage
    days = conn.execute(
        "SELECT COUNT(DISTINCT DATE(observed_at)) as c FROM market_observations"
    ).fetchone()["c"]
    print(f"Days of coverage:      {days}")

    conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="XGBoost Training Data Collector")
    parser.add_argument("--stats", action="store_true", help="Print database statistics")
    parser.add_argument("--verbose", action="store_true", help="Debug output")
    args = parser.parse_args()

    if args.stats:
        print_stats()
    else:
        n = collect_market_observations(verbose=args.verbose)
        print(f"[DONE] Collected {n} observations")


if __name__ == "__main__":
    main()
