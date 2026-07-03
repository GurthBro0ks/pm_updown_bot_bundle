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
    WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true ./venv/bin/python3 scripts/run_weather_strategy.py
"""

import argparse
import json
import logging
import math
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
from utils.pnl_database import get_db, record_trade
from utils.discord_notify import notify_weather_order_placed
from config import PROOF_DIR

logger = logging.getLogger(__name__)

# Strategy config
DEFAULT_BANKROLL = float(os.getenv("WEATHER_BANKROLL", "100.0"))
MAX_OPEN_ORDERS = int(os.getenv("WEATHER_MAX_OPEN_ORDERS", "5"))
WEATHER_MAX_DAILY_TRADES = int(os.getenv("WEATHER_MAX_DAILY_TRADES", "10"))
WEATHER_MAX_EXPOSURE_PER_CITY = float(os.getenv("WEATHER_MAX_EXPOSURE_PER_CITY", "1.00"))
WEATHER_MIN_EDGE_PCT = float(os.getenv("WEATHER_MIN_EDGE_PCT", "3.0"))

# Live-trading safety limits — independent from the main bot's limits.
# Set inline on the weather cron line, NOT in .env.
WEATHER_MAX_ORDERS_PER_RUN = int(os.getenv("WEATHER_MAX_ORDERS_PER_RUN", "1"))
WEATHER_MAX_DAILY_LOSS_USD = float(os.getenv("WEATHER_MAX_DAILY_LOSS_USD", "1.00"))
WEATHER_MAX_ORDER_USD = float(os.getenv("WEATHER_MAX_ORDER_USD", "0.25"))
WEATHER_MAX_RUN_EXPOSURE_USD = float(
    os.getenv("WEATHER_MAX_RUN_EXPOSURE_USD", os.getenv("WEATHER_MAX_NOTIONAL_PER_RUN_USD", "1.00"))
)
WEATHER_MAX_OPEN_EXPOSURE_USD = float(os.getenv("WEATHER_MAX_OPEN_EXPOSURE_USD", "2.00"))
WEATHER_MIN_TRADE_PRICE_CENTS = int(os.getenv("WEATHER_MIN_TRADE_PRICE_CENTS", "25"))

SAFE_WEATHER_MAX_ORDER_USD = 0.25
SAFE_WEATHER_MAX_RUN_EXPOSURE_USD = 1.00
SAFE_WEATHER_MAX_OPEN_EXPOSURE_USD = 2.00
SAFE_WEATHER_MAX_ORDERS_PER_RUN = 1


class WeatherLiveLimitError(ValueError):
    """Raised when weather live-mode tiny limits are absent or unsafe."""


def _parse_required_live_int(name: str, hard_cap: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raise WeatherLiveLimitError(f"{name} is required for live weather mode")
    try:
        value = int(raw)
    except ValueError as exc:
        raise WeatherLiveLimitError(f"{name} must be an integer") from exc
    if value <= 0 or value > hard_cap:
        raise WeatherLiveLimitError(f"{name}={value} exceeds live hard cap {hard_cap}")
    return value


def _parse_required_live_float(name: str, hard_cap: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raise WeatherLiveLimitError(f"{name} is required for live weather mode")
    try:
        value = float(raw)
    except ValueError as exc:
        raise WeatherLiveLimitError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or value <= 0 or value > hard_cap:
        raise WeatherLiveLimitError(f"{name}={value} exceeds live hard cap ${hard_cap:.2f}")
    return value


def get_weather_live_limits(require_env: bool) -> dict:
    """
    Return weather-only live limits.

    Live mode is intentionally fail-closed: cron must provide every tiny-limit
    variable explicitly and each value must be within the hard-coded safe cap.
    Dry-runs use safe defaults so validation can mirror live gates without
    requiring cron env.
    """
    if require_env:
        return {
            "max_orders_per_run": _parse_required_live_int(
                "WEATHER_MAX_ORDERS_PER_RUN",
                SAFE_WEATHER_MAX_ORDERS_PER_RUN,
            ),
            "max_order_usd": _parse_required_live_float(
                "WEATHER_MAX_ORDER_USD",
                SAFE_WEATHER_MAX_ORDER_USD,
            ),
            "max_run_exposure_usd": _parse_required_live_float(
                "WEATHER_MAX_RUN_EXPOSURE_USD",
                SAFE_WEATHER_MAX_RUN_EXPOSURE_USD,
            ),
            "max_open_exposure_usd": _parse_required_live_float(
                "WEATHER_MAX_OPEN_EXPOSURE_USD",
                SAFE_WEATHER_MAX_OPEN_EXPOSURE_USD,
            ),
        }

    return {
        "max_orders_per_run": min(WEATHER_MAX_ORDERS_PER_RUN, SAFE_WEATHER_MAX_ORDERS_PER_RUN),
        "max_order_usd": min(WEATHER_MAX_ORDER_USD, SAFE_WEATHER_MAX_ORDER_USD),
        "max_run_exposure_usd": min(WEATHER_MAX_RUN_EXPOSURE_USD, SAFE_WEATHER_MAX_RUN_EXPOSURE_USD),
        "max_open_exposure_usd": min(WEATHER_MAX_OPEN_EXPOSURE_USD, SAFE_WEATHER_MAX_OPEN_EXPOSURE_USD),
    }


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


def _weather_order_ticker(order: dict) -> str:
    return str(
        order.get("market_ticker")
        or order.get("ticker")
        or order.get("market_id")
        or ""
    )


def fetch_resting_weather_orders(client: KalshiOrderClient) -> list[dict]:
    """Fetch resting weather orders, failing closed on ambiguous data."""
    try:
        orders = client.get_orders(status="resting")
    except Exception as exc:
        raise WeatherLiveLimitError("failed to fetch resting orders for weather exposure") from exc

    if not isinstance(orders, list):
        raise WeatherLiveLimitError("resting orders response is not a list")

    weather_orders = []
    for order in orders:
        if not isinstance(order, dict):
            raise WeatherLiveLimitError("resting orders response contains a non-object order")

        ticker = _weather_order_ticker(order)
        if not ticker:
            raise WeatherLiveLimitError("resting order is missing market ticker")
        if ticker.startswith("KXHIGH"):
            weather_orders.append(order)

    return weather_orders


def count_open_weather_orders(client: KalshiOrderClient) -> int:
    """Count current open weather orders to respect MAX_OPEN_ORDERS."""
    return len(fetch_resting_weather_orders(client))


def _extract_order_quantity(order: dict) -> int:
    for key in ("remaining_count", "remaining_quantity", "quantity", "count"):
        raw = order.get(key)
        if raw is None:
            continue
        try:
            quantity = int(raw)
        except (TypeError, ValueError) as exc:
            raise WeatherLiveLimitError(f"open weather order has malformed {key}") from exc
        if quantity > 0:
            return quantity
    raise WeatherLiveLimitError("open weather order is missing remaining quantity")


def _extract_price_cents(order: dict) -> int:
    for key in ("price_cents", "yes_price_cents", "yes_price"):
        raw = order.get(key)
        if raw is None:
            continue
        try:
            price = int(round(float(raw)))
        except (TypeError, ValueError) as exc:
            raise WeatherLiveLimitError(f"open weather order has malformed {key}") from exc
        if 1 <= price <= 99:
            return price

    for key in ("price", "yes_price_dollars", "price_dollars"):
        raw = order.get(key)
        if raw is None:
            continue
        try:
            price = int(round(float(raw) * 100))
        except (TypeError, ValueError) as exc:
            raise WeatherLiveLimitError(f"open weather order has malformed {key}") from exc
        if 1 <= price <= 99:
            return price

    raise WeatherLiveLimitError("open weather order is missing price")


def estimate_weather_open_order_cost_usd(order: dict) -> float:
    """Estimate remaining capital at risk for one resting weather order."""
    side = str(order.get("contract_side") or order.get("side") or "yes").lower()
    price_cents = _extract_price_cents(order)
    # V2 event orders use bid/ask with price already equal to the resting
    # order's dollar cost. Older YES/NO-shaped records may carry a YES price,
    # where NO exposure is 100 - yes_price.
    cost_cents = 100 - price_cents if side == "no" else price_cents
    return _extract_order_quantity(order) * cost_cents / 100.0


def weather_open_exposure_usd(client: KalshiOrderClient) -> float:
    """Return current resting weather order exposure, failing closed if unclear."""
    orders = fetch_resting_weather_orders(client)
    total = 0.0
    for order in orders:
        total += estimate_weather_open_order_cost_usd(order)
    return total


def weather_open_order_state(client: KalshiOrderClient) -> tuple[int, float]:
    """Return (open_weather_order_count, open_weather_exposure_usd)."""
    orders = fetch_resting_weather_orders(client)
    exposure_usd = sum(estimate_weather_open_order_cost_usd(order) for order in orders)
    return len(orders), exposure_usd


def count_weather_trades_today() -> int:
    """Count live weather BUY trades recorded today in pnl.db."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = get_db()
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM trades
            WHERE phase = 'weather'
              AND action = 'BUY'
              AND substr(timestamp, 1, 10) = ?
            """,
            (today,),
        ).fetchone()
        return int(row["count"] if row else 0)
    except Exception as e:
        logger.warning(f"[WEATHER_RUNNER] Failed to count daily weather trades: {e}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def weather_daily_risk_usd() -> float:
    """
    Today's weather capital at risk: BUY notional placed today plus realized
    losses today, keyed by phase='weather' in pnl.db. Conservative on purpose
    (an unsettled buy counts as fully at risk) — independent from the main
    bot's daily-loss counter.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = get_db()
        row = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN action = 'BUY' THEN size_usd ELSE 0 END), 0) AS at_risk,
                   COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN -pnl_usd ELSE 0 END), 0) AS losses
            FROM trades
            WHERE phase = 'weather'
              AND substr(timestamp, 1, 10) = ?
            """,
            (today,),
        ).fetchone()
        return float(row["at_risk"]) + float(row["losses"]) if row else 0.0
    except Exception as e:
        logger.warning(f"[WEATHER_RUNNER] Failed to compute daily weather risk: {e}")
        return 0.0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def compute_order_params(signal: dict) -> tuple:
    """
    Derive order parameters from a signal.

    Returns (price_cents, cost_cents, quantity, cost_usd) where price_cents is
    the YES price sent to place_order, and cost_cents is what we actually pay
    per contract (YES price for yes-side, 100 - YES price for no-side).
    """
    price_cents = int(round(signal["market_price"] * 100))
    price_cents = max(1, min(99, price_cents))
    cost_cents = price_cents if signal["side"] == "yes" else 100 - price_cents

    quantity = max(1, int(signal["position_usd"] / (cost_cents / 100.0)))
    # Client hard-caps contracts per order; clamp so live orders aren't rejected
    quantity = min(quantity, KalshiOrderClient.MAX_QUANTITY)

    cost_usd = quantity * cost_cents / 100.0
    return price_cents, cost_cents, quantity, cost_usd


def apply_weather_risk_limits(signals: list, max_trades: int) -> list:
    """Apply runner-level weather safety caps before any order call."""
    selected = []
    exposure_by_city = {}
    min_edge_fraction = WEATHER_MIN_EDGE_PCT / 100.0

    for signal in signals:
        if len(selected) >= max_trades:
            break

        ticker = signal["ticker"]
        edge_pct = signal["edge_pct"]
        if edge_pct < min_edge_fraction:
            logger.info(
                f"[WEATHER_RUNNER] SKIP {ticker}: edge={edge_pct:.1%} < "
                f"{WEATHER_MIN_EDGE_PCT:.1f}% runner minimum"
            )
            continue

        city = signal.get("city_code") or signal.get("city") or "unknown"
        current_exposure = exposure_by_city.get(city, 0.0)
        next_exposure = current_exposure + signal["position_usd"]
        if next_exposure > WEATHER_MAX_EXPOSURE_PER_CITY:
            logger.info(
                f"[WEATHER_RUNNER] SKIP {ticker}: city exposure ${next_exposure:.2f} "
                f"> ${WEATHER_MAX_EXPOSURE_PER_CITY:.2f} cap for {city}"
            )
            continue

        exposure_by_city[city] = next_exposure
        selected.append(signal)

    return selected


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
    ensemble_prob = signal["ensemble_prob"]
    edge_pct = signal["edge_pct"] * 100  # Convert to percentage points

    price_cents, cost_cents, quantity, cost_usd = compute_order_params(signal)

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
            f"[WEATHER_LIVE] Placed {side.upper()} {ticker} @ {price_cents}c "
            f"qty={quantity} → {status} (id: {order_id})"
        )

        # Record to pnl.db
        record_trade(
            phase="weather",
            ticker=ticker,
            action="BUY",
            price=signal["market_price"],
            size_usd=cost_usd,
            pnl_usd=0,
            pnl_pct=0,
            signal_type="gfs_ensemble",
            market_category="weather",
            ai_probability=ensemble_prob,
            edge_pct=edge_pct,
            kelly_fraction=signal["kelly_fraction"],
            days_to_expiry=1,  # Weather markets are daily
            cascade_provider="gfs_ensemble",
        )

        # Discord notification (WEATHER-prefixed embed)
        notify_weather_order_placed(
            ticker=ticker,
            side=side,
            price_cents=price_cents,
            quantity=quantity,
            city=signal.get("city") or signal.get("city_code") or "unknown",
            ensemble_prob=ensemble_prob,
            market_price=signal["market_price"],
            edge_pct=edge_pct,
            threshold=signal.get("threshold"),
        )

        return True

    except SafetyLimitError as e:
        logger.warning(f"[WEATHER_RUNNER] SAFETY LIMIT: {ticker} — {e}")
        return False
    except Exception as e:
        logger.error(f"[WEATHER_RUNNER] ORDER FAILED: {ticker} — {e}")
        return False


def execute_weather_orders(
    client: KalshiOrderClient,
    signals: list,
    dry_run: bool = False,
) -> int:
    """
    Place orders for selected signals, enforcing per-run and daily-loss caps.

    Gates (applied in dry-run too, so dry-run mirrors live):
      - WEATHER_MAX_ORDERS_PER_RUN: max orders per invocation
      - WEATHER_MIN_TRADE_PRICE_CENTS: min cost per contract (skips lottery tickets)
      - WEATHER_MAX_NOTIONAL_PER_RUN_USD: max total cost per invocation
      - WEATHER_MAX_DAILY_LOSS_USD: max daily weather capital at risk (live only,
        via pnl.db counter — independent from the main bot's daily limit)

    Returns:
        Number of orders placed (or that would be placed in dry-run)
    """
    trades_placed = 0
    run_notional_usd = 0.0
    daily_risk_usd = 0.0 if dry_run else weather_daily_risk_usd()
    limits = get_weather_live_limits(require_env=not dry_run)

    if not dry_run and daily_risk_usd >= WEATHER_MAX_DAILY_LOSS_USD:
        logger.warning(
            f"[WEATHER_RUNNER] Daily weather risk ${daily_risk_usd:.2f} >= "
            f"${WEATHER_MAX_DAILY_LOSS_USD:.2f} cap — no orders this run"
        )
        return 0

    for signal in signals:
        if trades_placed >= limits["max_orders_per_run"]:
            logger.info(
                f"[WEATHER_RUNNER] Per-run order cap reached "
                f"({limits['max_orders_per_run']}) — stopping"
            )
            break

        ticker = signal["ticker"]
        price_cents, cost_cents, quantity, cost_usd = compute_order_params(signal)

        if cost_cents < WEATHER_MIN_TRADE_PRICE_CENTS:
            logger.info(
                f"[WEATHER_RUNNER] SKIP {ticker}: cost {cost_cents}c < "
                f"{WEATHER_MIN_TRADE_PRICE_CENTS}c minimum trade price"
            )
            continue

        if cost_usd > limits["max_order_usd"]:
            logger.info(
                f"[WEATHER_RUNNER] SKIP {ticker}: order cost ${cost_usd:.2f} > "
                f"${limits['max_order_usd']:.2f} max/order"
            )
            continue

        if run_notional_usd + cost_usd > limits["max_run_exposure_usd"]:
            logger.info(
                f"[WEATHER_RUNNER] SKIP {ticker}: run notional "
                f"${run_notional_usd + cost_usd:.2f} > "
                f"${limits['max_run_exposure_usd']:.2f} per-run cap"
            )
            continue

        if not dry_run and daily_risk_usd + run_notional_usd + cost_usd > WEATHER_MAX_DAILY_LOSS_USD:
            logger.info(
                f"[WEATHER_RUNNER] SKIP {ticker}: daily weather risk "
                f"${daily_risk_usd + run_notional_usd + cost_usd:.2f} > "
                f"${WEATHER_MAX_DAILY_LOSS_USD:.2f} cap"
            )
            continue

        if place_weather_order(client=client, signal=signal, dry_run=dry_run):
            trades_placed += 1
            run_notional_usd += cost_usd

    return trades_placed


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

    # Weather is fail-closed: unset WEATHER_DRY_RUN means dry-run. Live mode
    # requires explicit WEATHER_DRY_RUN=false, WEATHER_LIVE_ENABLED=true, and
    # no --dry-run flag.
    env_value = os.getenv("WEATHER_DRY_RUN", "true").strip().lower()
    live_enabled = os.getenv("WEATHER_LIVE_ENABLED", "").strip().lower() == "true"
    env_allows_live = env_value == "false" and live_enabled
    dry_run = args.dry_run or not env_allows_live

    log_file = setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 70)
    logger.info("WEATHER STRATEGY — GFS Ensemble Signals")
    logger.info(f"Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
    logger.info(f"Bankroll: ${args.bankroll:.2f}")
    if not dry_run:
        try:
            limits = get_weather_live_limits(require_env=True)
        except WeatherLiveLimitError as exc:
            logger.error(f"[WEATHER_RUNNER] Live weather limits invalid: {exc}")
            return 1

        logger.info(
            f"Limits: {limits['max_orders_per_run']} orders/run, "
            f"${limits['max_order_usd']:.2f} max/order, "
            f"${limits['max_run_exposure_usd']:.2f} notional/run, "
            f"${limits['max_open_exposure_usd']:.2f} open exposure, "
            f"${WEATHER_MAX_DAILY_LOSS_USD:.2f} daily loss, "
            f"min {WEATHER_MIN_TRADE_PRICE_CENTS}c/contract"
        )
    logger.info("=" * 70)

    # Step 1: Generate signals
    logger.info("[1/3] Generating weather signals...")
    signals = generate_weather_signals(
        bankroll=args.bankroll,
        dry_run=dry_run,
    )

    if not signals:
        logger.info("[WEATHER_RUNNER] No signals generated — nothing to trade")
        generate_proof(signals, 0, dry_run)
        return 0

    logger.info(f"[WEATHER_RUNNER] {len(signals)} signals generated")

    # Step 2: Check risk gates
    logger.info("[2/3] Checking risk gates...")
    client = None
    daily_trades = 0
    if not dry_run:
        client = get_order_client()
        if client is None:
            logger.error("[WEATHER_RUNNER] Cannot place orders: client init failed")
            return 1

        try:
            open_count, open_exposure_usd = weather_open_order_state(client)
        except WeatherLiveLimitError as exc:
            logger.error(f"[WEATHER_RUNNER] Open weather exposure check failed: {exc}")
            return 1

        if open_exposure_usd >= limits["max_open_exposure_usd"]:
            logger.warning(
                f"[WEATHER_RUNNER] Open weather exposure ${open_exposure_usd:.2f} >= "
                f"${limits['max_open_exposure_usd']:.2f} cap — skipping"
            )
            generate_proof(signals, 0, dry_run)
            return 0

        logger.info(f"[WEATHER_RUNNER] Open weather orders: {open_count}/{MAX_OPEN_ORDERS}")
        logger.info(
            f"[WEATHER_RUNNER] Open weather exposure: "
            f"${open_exposure_usd:.2f}/${limits['max_open_exposure_usd']:.2f}"
        )

        if open_count >= MAX_OPEN_ORDERS:
            logger.warning(f"[WEATHER_RUNNER] Max open orders reached ({MAX_OPEN_ORDERS}) — skipping")
            generate_proof(signals, 0, dry_run)
            return 0

        daily_trades = count_weather_trades_today()
        logger.info(f"[WEATHER_RUNNER] Daily weather trades: {daily_trades}/{WEATHER_MAX_DAILY_TRADES}")

        if daily_trades >= WEATHER_MAX_DAILY_TRADES:
            logger.warning(
                f"[WEATHER_RUNNER] Max daily weather trades reached ({WEATHER_MAX_DAILY_TRADES}) — skipping"
            )
            generate_proof(signals, 0, dry_run)
            return 0

    # Step 3: Place orders
    logger.info("[3/3] Placing orders...")
    open_slots = MAX_OPEN_ORDERS - (open_count if client else 0)
    daily_slots = WEATHER_MAX_DAILY_TRADES - daily_trades
    max_to_place = max(0, min(open_slots, daily_slots))
    selected_signals = apply_weather_risk_limits(signals, max_to_place)

    trades_placed = execute_weather_orders(
        client=client,
        signals=selected_signals,
        dry_run=dry_run,
    )

    # Summary
    logger.info("=" * 70)
    logger.info("WEATHER STRATEGY COMPLETE")
    logger.info(f"Signals found: {len(signals)}")
    logger.info(f"Trades placed: {trades_placed}")
    logger.info(f"Log: {log_file}")
    logger.info("=" * 70)

    # Generate proof
    generate_proof(signals, trades_placed, dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
