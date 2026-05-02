#!/usr/bin/env python3
"""
Kalshi Orderbook Signal Collection CLI

Fetches orderbook depth and trade tape snapshots for Kalshi markets
and stores them in a SQLite database.

Usage:
    python3 scripts/collect_orderbook.py [--max-markets 20] [--dry-run]

Environment variables:
    ORDERBOOK_WALL_MIN_USD (default: 100)
    ORDERBOOK_LARGE_FILL_USD (default: 50)
    ORDERBOOK_VOLUME_SPIKE_MULT (default: 3.0)
    ORDERBOOK_DEPTH_IMBALANCE_RATIO (default: 3.0)
    ORDERBOOK_SPREAD_COMPRESSION_CENTS (default: 0.01)
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from signals.orderbook_signal import collect_snapshot
from signals.orderbook_store import store_snapshot
from utils.kalshi import fetch_kalshi_markets

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _select_markets(markets: list, max_markets: int) -> list:
    """Select top markets by open_interest for scanning.

    If open_interest is unavailable, fall back to volume_24h.
    """
    scored = []
    for m in markets:
        oi = m.get("open_interest") or 0.0
        vol = m.get("volume_24h") or 0.0
        score = oi if oi > 0 else vol
        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:max_markets]]


def main():
    parser = argparse.ArgumentParser(description="Collect Kalshi orderbook signals")
    parser.add_argument(
        "--max-markets",
        type=int,
        default=20,
        help="Maximum number of markets to scan (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and detect events but do not store to DB",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between per-market API calls (default: 0.5)",
    )
    args = parser.parse_args()

    logger.info("[COLLECT_ORDERBOOK] Starting collection")
    logger.info(f"[COLLECT_ORDERBOOK] max_markets={args.max_markets} dry_run={args.dry_run}")

    # Fetch markets
    logger.info("[COLLECT_ORDERBOOK] Fetching market list...")
    markets = fetch_kalshi_markets()
    if not markets:
        logger.error("[COLLECT_ORDERBOOK] No markets found. Exiting.")
        sys.exit(2)

    logger.info(f"[COLLECT_ORDERBOOK] Fetched {len(markets)} markets")

    # Select top markets
    selected = _select_markets(markets, args.max_markets)
    logger.info(f"[COLLECT_ORDERBOOK] Selected top {len(selected)} markets by open_interest/volume")

    total_events = 0
    stored_count = 0
    error_count = 0

    for idx, market in enumerate(selected, 1):
        ticker = market.get("ticker", "")
        question = market.get("question", "")
        yes_bid = market.get("yes_bid")
        yes_ask = market.get("yes_ask")
        volume_24h = market.get("volume_24h")
        open_interest = market.get("open_interest")

        logger.info(f"[COLLECT_ORDERBOOK] [{idx}/{len(selected)}] Scanning {ticker}")

        try:
            snapshot = collect_snapshot(
                ticker=ticker,
                question=question,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                volume_24h=volume_24h,
                open_interest=open_interest,
                sleep_between_calls=args.sleep,
            )

            if not snapshot:
                logger.warning(f"[COLLECT_ORDERBOOK] Empty snapshot for {ticker}")
                error_count += 1
                continue

            event_count = len(snapshot.get("events", []))
            total_events += event_count

            if not args.dry_run:
                row_id = store_snapshot(snapshot=snapshot)
                stored_count += 1
                logger.info(
                    f"[COLLECT_ORDERBOOK] Stored {ticker} id={row_id} events={event_count}"
                )
            else:
                logger.info(
                    f"[COLLECT_ORDERBOOK] Dry-run {ticker} events={event_count} (not stored)"
                )

        except Exception as e:
            logger.error(f"[COLLECT_ORDERBOOK] Error scanning {ticker}: {e}")
            error_count += 1

        # Rate limit politeness between markets
        if idx < len(selected) and args.sleep > 0:
            time.sleep(args.sleep)

    logger.info("=" * 60)
    logger.info("[COLLECT_ORDERBOOK] Collection complete")
    logger.info(f"[COLLECT_ORDERBOOK] Markets scanned: {len(selected)}")
    logger.info(f"[COLLECT_ORDERBOOK] Snapshots stored: {stored_count}")
    logger.info(f"[COLLECT_ORDERBOOK] Total events detected: {total_events}")
    logger.info(f"[COLLECT_ORDERBOOK] Errors: {error_count}")
    logger.info("=" * 60)

    if error_count > 0 and stored_count == 0:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
