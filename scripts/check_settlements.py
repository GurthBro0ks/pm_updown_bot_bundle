#!/usr/bin/env python3
"""
Settlement Checker — Polls Kalshi API for fills and settlements.

Runs every 30 minutes via cron.
- Detects executed (filled) orders and updates DB
- Detects market settlements and calculates realized PNL
- Sends Discord won/lost alerts
- Flags stale resting orders (>48h)

Idempotent: safe to re-run via kalshi_order_id dedup.
"""

import os
import sys
import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from dotenv import load_dotenv

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.kalshi_orders import KalshiOrderClient
from utils.discord_notify import notify_order_won, notify_order_lost
from utils.pnl_database import DB_PATH, get_db

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_PATH = LOG_DIR / f"settlement_check_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

STALE_HOURS = 48
MATCH_WINDOW_MINUTES = 10  # ± window for matching order timestamp to DB record

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp from Kalshi API."""
    if not ts_str:
        return None
    # Handle both 'Z' suffix and '+00:00'
    ts_str = ts_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def db_ts_to_dt(ts_str: str) -> Optional[datetime]:
    """Parse SQLite timestamp (assumed UTC)."""
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def get_executed_orders(client: KalshiOrderClient, lookback_days: int = 30) -> List[Dict]:
    """Fetch executed orders from Kalshi within lookback window."""
    try:
        orders = client.get_orders("all")
        if not isinstance(orders, list):
            logger.error(f"Expected list from get_orders, got {type(orders)}")
            return []
    except Exception as e:
        logger.error(f"Failed to fetch orders: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    executed = []
    for o in orders:
        if o.get("status") != "executed":
            continue
        created = parse_ts(o.get("created_time", ""))
        if created and created >= cutoff:
            executed.append(o)
    return executed


def get_open_db_trades(conn: sqlite3.Connection, lookback_days: int = 30) -> List[sqlite3.Row]:
    """Get DB trades with status='open' within lookback window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT * FROM trades WHERE status = 'open' AND timestamp >= ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()
    return rows


def get_filled_db_trades(conn: sqlite3.Connection, lookback_days: int = 60) -> List[sqlite3.Row]:
    """Get DB trades with status='filled' that might settle soon."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT * FROM trades WHERE status = 'filled' AND timestamp >= ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()
    return rows


def find_matching_trade(order: Dict, db_trades: List[sqlite3.Row]) -> Optional[sqlite3.Row]:
    """Match an executed Kalshi order to a DB trade by ticker + timestamp proximity."""
    order_ticker = order.get("ticker", "")
    order_ts = parse_ts(order.get("created_time", ""))
    if not order_ts:
        return None

    best_match = None
    best_delta = None

    for row in db_trades:
        if row["ticker"] != order_ticker:
            continue
        db_ts = db_ts_to_dt(row["timestamp"])
        if not db_ts:
            continue
        delta = abs((order_ts - db_ts).total_seconds())
        if delta <= MATCH_WINDOW_MINUTES * 60:
            if best_match is None or delta < best_delta:
                best_match = row
                best_delta = delta

    return best_match


def calculate_fill_price(order: Dict) -> Optional[float]:
    """Derive actual fill price per contract in dollars."""
    try:
        fill_cost = float(order.get("maker_fill_cost_dollars", 0)) + float(order.get("taker_fill_cost_dollars", 0))
        fill_count = float(order.get("fill_count_fp", 0))
        if fill_count > 0:
            return fill_cost / fill_count
        # Fallback to order price fields
        side = order.get("side", "yes")
        if side == "yes":
            price = float(order.get("yes_price_dollars", 0))
        else:
            price = float(order.get("no_price_dollars", 0))
        return price if price > 0 else None
    except Exception:
        return None


def check_market_settlement(client: KalshiOrderClient, ticker: str) -> Optional[Dict]:
    """Query market status. Returns dict with settled: bool, result: str, price: float, ts: str."""
    try:
        data = client._request("GET", f"/markets/{ticker}")
        market = data.get("market", {})
        status = market.get("status", "")
        result = market.get("result", "")
        settlement_ts = market.get("settlement_ts", "")
        settlement_price = market.get("settlement_value_dollars", "")

        if status == "finalized" and result:
            return {
                "settled": True,
                "result": result.lower(),
                "price": float(settlement_price) if settlement_price else (1.0 if result.lower() == "yes" else 0.0),
                "ts": settlement_ts,
            }
        return {"settled": False}
    except Exception as e:
        logger.warning(f"Market query failed for {ticker}: {e}")
        return {"settled": False}


def calculate_pnl(side: str, fill_price: float, quantity: float, market_result: str) -> float:
    """Calculate realized PNL for binary contract."""
    side = side.lower()
    result = market_result.lower()
    if side == "yes":
        if result == "yes":
            return (1.0 - fill_price) * quantity
        else:
            return -fill_price * quantity
    else:  # no
        if result == "no":
            return (1.0 - fill_price) * quantity
        else:
            return -fill_price * quantity


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def process_fills(client: KalshiOrderClient, conn: sqlite3.Connection) -> int:
    """Detect new fills, update DB, return count."""
    orders = get_executed_orders(client)
    logger.info(f"Found {len(orders)} executed orders in last 30 days")

    db_trades = get_open_db_trades(conn)
    logger.info(f"Found {len(db_trades)} open DB trades")

    matched = 0
    for order in orders:
        order_id = order.get("order_id", "")
        # Skip if already recorded
        existing = conn.execute(
            "SELECT 1 FROM trades WHERE kalshi_order_id = ?", (order_id,)
        ).fetchone()
        if existing:
            continue

        match = find_matching_trade(order, db_trades)
        if not match:
            logger.debug(f"No DB match for order {order_id} {order.get('ticker')}")
            continue

        fill_price = calculate_fill_price(order)
        fill_ts = order.get("last_update_time", order.get("created_time", ""))
        ticker = order.get("ticker", "")

        conn.execute(
            """
            UPDATE trades
            SET status = 'filled',
                fill_time = ?,
                kalshi_order_id = ?,
                kalshi_market_id = ?,
                price = COALESCE(?, price)
            WHERE id = ?
            """,
            (fill_ts, order_id, ticker, fill_price, match["id"]),
        )
        conn.commit()
        logger.info(f"Recorded fill: {ticker} order={order_id} db_id={match['id']}")
        matched += 1

    return matched


def process_settlements(client: KalshiOrderClient, conn: sqlite3.Connection) -> Tuple[int, int]:
    """Check filled trades for market settlements. Returns (wins, losses)."""
    trades = get_filled_db_trades(conn)
    logger.info(f"Checking {len(trades)} filled trades for settlements")

    wins = 0
    losses = 0
    for row in trades:
        ticker = row["ticker"]
        settlement = check_market_settlement(client, ticker)
        if not settlement["settled"]:
            continue

        # Determine side (default yes for legacy records)
        side = "yes"  # All current strategy orders are YES
        fill_price = row["price"] or 0
        quantity = 1.0  # Bot always buys 1 contract

        pnl = calculate_pnl(side, fill_price, quantity, settlement["result"])
        status = "settled_won" if pnl >= 0 else "settled_lost"

        conn.execute(
            """
            UPDATE trades
            SET status = ?,
                settle_time = ?,
                settled_price = ?,
                realized_pnl_usd = ?
            WHERE id = ?
            """,
            (status, settlement["ts"], settlement["price"], round(pnl, 4), row["id"]),
        )
        conn.commit()

        if status == "settled_won":
            wins += 1
            logger.info(f"SETTLED WON: {ticker} PNL=${pnl:+.4f}")
            try:
                notify_order_won(
                    ticker=ticker,
                    side=side.upper(),
                    price_cents=int(fill_price * 100),
                    pnl_usd=round(pnl, 2),
                    settled_price=int(settlement["price"] * 100) if settlement["price"] is not None else None,
                )
            except Exception as e:
                logger.error(f"Discord notify won failed: {e}")
        else:
            losses += 1
            logger.info(f"SETTLED LOST: {ticker} PNL=${pnl:+.4f}")
            try:
                notify_order_lost(
                    ticker=ticker,
                    side=side.upper(),
                    price_cents=int(fill_price * 100),
                    pnl_usd=round(pnl, 2),
                    settled_price=int(settlement["price"] * 100) if settlement["price"] is not None else None,
                )
            except Exception as e:
                logger.error(f"Discord notify lost failed: {e}")

    return wins, losses


def flag_stale_orders(client: KalshiOrderClient, conn: sqlite3.Connection) -> int:
    """Flag open orders that have been resting >48h. Returns count."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    stale = conn.execute(
        "SELECT * FROM trades WHERE status = 'open' AND timestamp < ?",
        (cutoff,),
    ).fetchall()

    if not stale:
        return 0

    # Get current resting orders from Kalshi
    try:
        resting_orders = client.get_orders("resting")
        resting_tickers = {o.get("ticker"): o for o in resting_orders if isinstance(resting_orders, list)}
    except Exception as e:
        logger.error(f"Failed to fetch resting orders: {e}")
        resting_tickers = {}

    flagged = 0
    for row in stale:
        ticker = row["ticker"]
        if ticker in resting_tickers:
            logger.warning(f"STALE ORDER: {ticker} open since {row['timestamp']} (>48h)")
            flagged += 1

    return flagged


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("=== Settlement Check Started ===")
    client = KalshiOrderClient()
    conn = get_db()

    try:
        fills = process_fills(client, conn)
        wins, losses = process_settlements(client, conn)
        stale = flag_stale_orders(client, conn)

        logger.info(
            f"=== Settlement Check Complete === fills={fills} wins={wins} losses={losses} stale={stale}"
        )
    except Exception as e:
        logger.exception("Settlement check failed")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
