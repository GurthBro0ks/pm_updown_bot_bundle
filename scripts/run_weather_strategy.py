#!/usr/bin/env python3
"""
Weather Strategy Runner — Cron-friendly execution script

Runs the GFS ensemble weather trading strategy:
1. Generates signals via strategies/weather_signals.py
2. Places orders via KalshiOrderClient (if not dry-run)
3. Logs trades to pnl.db
4. Sends Discord notifications
5. Generates proof files

Usage:
    ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run
    ./venv/bin/python3 scripts/run_weather_strategy.py
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project to path
sys.path.insert(0, "/opt/slimy/pm_updown_bot_bundle")

from dotenv import load_dotenv
load_dotenv()

from strategies.weather_signals import generate_weather_signals
from utils.kalshi_orders import KalshiOrderClient, SafetyLimitError
from utils.pnl_database import record_trade
from utils.discord_notify import notify_order_placed
from config import PROOF_DIR

logger = logging.getLogger(__name__)

# Strategy config
DEFAULT_BANKROLL = float(os.getenv("WEATHER_BANKROLL", "100.0"))
MAX_OPEN_ORDERS = int(os.getenv("WEATHER_MAX_OPEN_ORDERS", "5"))
DRY_RUN = False


def setup_logging():
    """Configure logging to file and stdout."""
    log_dir = Path("/opt/slimy/pm_updown_bot_bundle/logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"weather_strategy_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    return log_file


def get_order_client() -> KalshiOrderClient:
    """Initialize Kalshi order client."""
    try:
        return KalshiOrderClient()
    except Exception as e:
        logger.error(f"[WEATHER_RUNNER] Failed to initialize KalshiOrderClient: {e}")
        return None


def count_open_weather_orders(client: KalshiOrderClient) -> int:
    """Count current open weather orders to respect MAX_OPEN_ORDERS."""
    try:
        orders = client.get_orders(status="resting")
        # Filter to weather-related tickers (KXHIGH*)
        weather_orders = [o for o in orders if o.get("market_ticker", "").startswith("KXHIGH")]
        return len(weather_orders)
    except Exception as e:
        logger.warning(f"[WEATHER_RUNNER] Failed to count open orders: {e}")
        return 0


def place_weather_order(
    client: KalshiOrderClient,
    signal: dict,
    dry_run: bool = False,
) -> bool:
    """
    Place a single weather order.

    Args:
        client: KalshiOrderClient instance
        signal: Signal dict from generate_weather_signals()
        dry_run: If True, don't actually place order

    Returns:
        True if order was placed (or would be placed in dry-run)
    """
    ticker = signal["ticker"]
    side = signal["side"]
    position_usd = signal["position_usd"]
    ensemble_prob = signal["ensemble_prob"]
    edge_pct = signal["edge_pct"] * 100  # Convert to percentage points

    # Convert USD to cents for order
    price_cents = int(round(signal["market_price"] * 100))
    price_cents = max(1, min(99, price_cents))

    # Quantity: how many contracts at this price = position_usd
    quantity = max(1, int(position_usd / (price_cents / 100.0)))

    if dry_run:
        logger.info(
            f"[WEATHER_RUNNER] DRY-RUN: Would place {side.upper()} {ticker} "
            f"qty={quantity} @ {price_cents}¢ "
            f"(ensemble={ensemble_prob:.1%}, edge={edge_pct:.1f}%)"
        )
        return True

    try:
        result = client.place_order(
            ticker=ticker,
            side=side,
            quantity=quantity,
            price_cents=price_cents,
        )
        order_id = result.get("order_id", "unknown")
        status = result.get("status", "unknown")

        logger.info(
            f"[WEATHER_RUNNER] ORDER PLACED: {side.upper()} {ticker} "
            f"qty={quantity} @ {price_cents}¢ → {status} (id: {order_id})"
        )

        # Record to pnl.db
        record_trade(
            phase="weather",
            ticker=ticker,
            action="BUY",
            price=signal["market_price"],
            size_usd=position_usd,
            pnl_usd=0,
            pnl_pct=0,
            ai_probability=ensemble_prob,
            edge_pct=edge_pct,
            kelly_fraction=signal["kelly_fraction"],
            days_to_expiry=1,  # Weather markets are daily
            cascade_provider="gfs_ensemble",
        )

        # Discord notification
        notify_order_placed(
            ticker=ticker,
            side=side,
            price_cents=price_cents / 100.0,  # notify_order_placed expects dollars
            quantity=quantity,
            ai_probability=ensemble_prob,
            edge_pct=edge_pct,
            kelly_fraction=signal["kelly_fraction"],
            cascade_provider="gfs_ensemble",
            days_to_expiry=1,
        )

        return True

    except SafetyLimitError as e:
        logger.warning(f"[WEATHER_RUNNER] SAFETY LIMIT: {ticker} — {e}")
        return False
    except Exception as e:
        logger.error(f"[WEATHER_RUNNER] ORDER FAILED: {ticker} — {e}")
        return False


def generate_proof(signals: list, trades_placed: int, dry_run: bool):
    """Generate proof JSON file."""
    proof_id = f"weather_ensemble_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "strategy": "gfs_ensemble_weather",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "signals_count": len(signals),
        "trades_placed": trades_placed,
        "signals": signals,
    }

    try:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        proof_path = PROOF_DIR / f"{proof_id}.json"
        with open(proof_path, "w") as f:
            json.dump(proof_data, f, indent=2)
        logger.info(f"[WEATHER_RUNNER] Proof saved: {proof_path}")
    except Exception as e:
        logger.error(f"[WEATHER_RUNNER] Failed to write proof: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run GFS ensemble weather trading strategy")
    parser.add_argument("--dry-run", action="store_true", help="Don't place real orders")
    parser.add_argument("--bankroll", type=float, default=DEFAULT_BANKROLL, help="Bankroll for sizing")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    log_file = setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 70)
    logger.info("WEATHER STRATEGY — GFS Ensemble Signals")
    logger.info(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    logger.info(f"Bankroll: ${args.bankroll:.2f}")
    logger.info("=" * 70)

    # Step 1: Generate signals
    logger.info("[1/3] Generating weather signals...")
    signals = generate_weather_signals(
        bankroll=args.bankroll,
        dry_run=args.dry_run,
    )

    if not signals:
        logger.info("[WEATHER_RUNNER] No signals generated — nothing to trade")
        generate_proof(signals, 0, args.dry_run)
        return 0

    logger.info(f"[WEATHER_RUNNER] {len(signals)} signals generated")

    # Step 2: Check risk gates
    logger.info("[2/3] Checking risk gates...")
    client = None
    if not args.dry_run:
        client = get_order_client()
        if client is None:
            logger.error("[WEATHER_RUNNER] Cannot place orders: client init failed")
            return 1

        open_count = count_open_weather_orders(client)
        logger.info(f"[WEATHER_RUNNER] Open weather orders: {open_count}/{MAX_OPEN_ORDERS}")

        if open_count >= MAX_OPEN_ORDERS:
            logger.warning(f"[WEATHER_RUNNER] Max open orders reached ({MAX_OPEN_ORDERS}) — skipping")
            generate_proof(signals, 0, args.dry_run)
            return 0

    # Step 3: Place orders
    logger.info("[3/3] Placing orders...")
    trades_placed = 0
    max_to_place = MAX_OPEN_ORDERS - (open_count if client else 0)

    for signal in signals[:max_to_place]:
        success = place_weather_order(
            client=client,
            signal=signal,
            dry_run=args.dry_run,
        )
        if success:
            trades_placed += 1

    # Summary
    logger.info("=" * 70)
    logger.info("WEATHER STRATEGY COMPLETE")
    logger.info(f"Signals found: {len(signals)}")
    logger.info(f"Trades placed: {trades_placed}")
    logger.info(f"Log: {log_file}")
    logger.info("=" * 70)

    # Generate proof
    generate_proof(signals, trades_placed, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
