"""
Kalshi Orderbook + Trade Tape Signal Collector

Lightweight read-only module that captures orderbook depth and public trade
snapshots. Designed to bolt onto the existing cron cycle without interfering
with trading logic.

All Kalshi string fields are parsed via Decimal, then converted to float at
the output boundary. Present zero ("0.0000") is valid, not missing.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests

from utils.kalshi import (
    _candidate_kalshi_base_urls,
    get_kalshi_headers,
    KALSHI_CANONICAL_BASE_URL,
)

logger = logging.getLogger(__name__)

# Configurable thresholds (env vars with defaults)
WALL_MIN_USD = float(os.getenv("ORDERBOOK_WALL_MIN_USD", "100"))
LARGE_FILL_USD = float(os.getenv("ORDERBOOK_LARGE_FILL_USD", "50"))
VOLUME_SPIKE_MULT = float(os.getenv("ORDERBOOK_VOLUME_SPIKE_MULT", "3.0"))
DEPTH_IMBALANCE_RATIO = float(os.getenv("ORDERBOOK_DEPTH_IMBALANCE_RATIO", "3.0"))
SPREAD_COMPRESSION_CENTS = float(os.getenv("ORDERBOOK_SPREAD_COMPRESSION_CENTS", "0.01"))


def _parse_decimal_str(value: Any) -> Optional[float]:
    """Parse a Kalshi API string value via Decimal, then float.

    Returns None for missing/invalid. Returns 0.0 for present zero.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return float(Decimal(value.strip()))
        except (InvalidOperation, ValueError):
            return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_credentials():
    """Load Kalshi API credentials. Returns (api_key, private_key) or (None, None)."""
    from cryptography.hazmat.primitives import serialization

    api_key = os.getenv("KALSHI_KEY")
    if not api_key:
        logger.warning("KALSHI_KEY not set")
        return None, None

    secret_file = os.getenv("KALSHI_SECRET_FILE", "./kalshi_private_key.pem")
    try:
        with open(secret_file, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    except Exception as e:
        logger.error(f"Failed to load Kalshi private key: {e}")
        return None, None

    return api_key, private_key


def fetch_orderbook(ticker: str, api_key: str, private_key) -> dict:
    """Fetch orderbook depth for a single market.

    Kalshi returns yes_dollars and no_dollars arrays: [[price, size], ...]
    Note: Kalshi only returns bids. yes bid at X = no ask at (100-X).
    """
    path = f"/markets/{ticker}/orderbook"
    headers = get_kalshi_headers("GET", path, api_key, private_key)

    for base_url in _candidate_kalshi_base_urls():
        try:
            resp = requests.get(
                f"{base_url}/trade-api/v2{path}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data
            logger.warning(
                f"[ORDERBOOK] {ticker} status={resp.status_code} base={base_url}"
            )
        except Exception as e:
            logger.warning(f"[ORDERBOOK] {ticker} error on {base_url}: {e}")

    return {}


def fetch_trades(ticker: str, api_key: str, private_key, limit: int = 100) -> list:
    """Fetch recent public trades for a single market."""
    path = f"/markets/trades?ticker={ticker}&limit={limit}"
    headers = get_kalshi_headers("GET", path, api_key, private_key)

    for base_url in _candidate_kalshi_base_urls():
        try:
            resp = requests.get(
                f"{base_url}/trade-api/v2{path}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("trades", [])
            logger.warning(
                f"[TRADES] {ticker} status={resp.status_code} base={base_url}"
            )
        except Exception as e:
            logger.warning(f"[TRADES] {ticker} error on {base_url}: {e}")

    return []


def _detect_events(
    yes_bid: Optional[float],
    yes_ask: Optional[float],
    volume_24h: Optional[float],
    open_interest: Optional[float],
    orderbook: dict,
    trades: list,
) -> list:
    """Detect notable events from a snapshot."""
    events = []

    # 1. large_wall: any single price level with size > threshold
    yes_bids = orderbook.get("yes_bids", [])
    no_bids = orderbook.get("no_bids", [])

    for price, size in yes_bids:
        if size is not None and size >= WALL_MIN_USD:
            events.append({
                "type": "large_wall",
                "side": "yes",
                "details": {
                    "price": price,
                    "size": size,
                    "threshold": WALL_MIN_USD,
                },
                "score": min(1.0, size / (WALL_MIN_USD * 5.0)),
            })

    for price, size in no_bids:
        if size is not None and size >= WALL_MIN_USD:
            events.append({
                "type": "large_wall",
                "side": "no",
                "details": {
                    "price": price,
                    "size": size,
                    "threshold": WALL_MIN_USD,
                },
                "score": min(1.0, size / (WALL_MIN_USD * 5.0)),
            })

    # 2. volume_spike: recent trade volume vs 24h volume
    if volume_24h is not None and volume_24h > 0 and trades:
        recent_volume = sum(
            _parse_decimal_str(t.get("count_fp", 0)) or 0.0
            for t in trades
        )
        if recent_volume > 0:
            ratio = recent_volume / volume_24h
            if ratio >= VOLUME_SPIKE_MULT:
                events.append({
                    "type": "volume_spike",
                    "side": "both",
                    "details": {
                        "recent_volume": recent_volume,
                        "volume_24h": volume_24h,
                        "ratio": ratio,
                        "threshold": VOLUME_SPIKE_MULT,
                    },
                    "score": min(1.0, ratio / (VOLUME_SPIKE_MULT * 2.0)),
                })

    # 3. large_fill: any single trade count > threshold
    for trade in trades:
        count = _parse_decimal_str(trade.get("count_fp"))
        if count is not None and count >= LARGE_FILL_USD:
            events.append({
                "type": "large_fill",
                "side": trade.get("taker_side", "unknown"),
                "details": {
                    "trade_id": trade.get("trade_id"),
                    "count": count,
                    "price": _parse_decimal_str(trade.get("yes_price_dollars")),
                    "threshold": LARGE_FILL_USD,
                },
                "score": min(1.0, count / (LARGE_FILL_USD * 5.0)),
            })

    # 4. spread_compression: bid/ask within 1 cent
    if yes_bid is not None and yes_ask is not None:
        spread = yes_ask - yes_bid
        if spread >= 0 and spread <= SPREAD_COMPRESSION_CENTS:
            events.append({
                "type": "spread_compression",
                "side": "both",
                "details": {
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "spread_cents": spread,
                    "threshold": SPREAD_COMPRESSION_CENTS,
                },
                "score": min(1.0, 1.0 - (spread / SPREAD_COMPRESSION_CENTS)),
            })

    # 5. depth_imbalance: total yes bid depth vs no bid depth
    yes_total = sum(size for _, size in yes_bids if size is not None)
    no_total = sum(size for _, size in no_bids if size is not None)
    if yes_total > 0 and no_total > 0:
        ratio = yes_total / no_total
        if ratio >= DEPTH_IMBALANCE_RATIO:
            events.append({
                "type": "depth_imbalance",
                "side": "yes",
                "details": {
                    "yes_depth": yes_total,
                    "no_depth": no_total,
                    "ratio": ratio,
                    "threshold": DEPTH_IMBALANCE_RATIO,
                },
                "score": min(1.0, ratio / (DEPTH_IMBALANCE_RATIO * 2.0)),
            })
        elif ratio <= 1.0 / DEPTH_IMBALANCE_RATIO:
            events.append({
                "type": "depth_imbalance",
                "side": "no",
                "details": {
                    "yes_depth": yes_total,
                    "no_depth": no_total,
                    "ratio": ratio,
                    "threshold": DEPTH_IMBALANCE_RATIO,
                },
                "score": min(1.0, (1.0 / ratio) / (DEPTH_IMBALANCE_RATIO * 2.0)),
            })

    return events


def _parse_orderbook(data: dict) -> dict:
    """Parse raw Kalshi orderbook response into yes/no bid arrays."""
    yes_raw = data.get("yes_dollars", [])
    no_raw = data.get("no_dollars", [])

    yes_bids = []
    for level in yes_raw:
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            price = _parse_decimal_str(level[0])
            size = _parse_decimal_str(level[1])
            if price is not None and size is not None:
                yes_bids.append([price, size])

    no_bids = []
    for level in no_raw:
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            price = _parse_decimal_str(level[0])
            size = _parse_decimal_str(level[1])
            if price is not None and size is not None:
                no_bids.append([price, size])

    # Sort by price descending (bids)
    yes_bids.sort(key=lambda x: x[0], reverse=True)
    no_bids.sort(key=lambda x: x[0], reverse=True)

    return {
        "yes_bids": yes_bids[:10],
        "no_bids": no_bids[:10],
    }


def _parse_trades(trades: list) -> list:
    """Parse raw Kalshi trades into normalized format."""
    parsed = []
    for t in trades:
        count = _parse_decimal_str(t.get("count_fp"))
        if count is None:
            continue
        parsed.append({
            "trade_id": t.get("trade_id", ""),
            "count_fp": count,
            "yes_price_dollars": _parse_decimal_str(t.get("yes_price_dollars")),
            "no_price_dollars": _parse_decimal_str(t.get("no_price_dollars")),
            "taker_side": t.get("taker_side", ""),
            "created_time": t.get("created_time", ""),
        })
    return parsed


def collect_snapshot(
    ticker: str,
    question: Optional[str] = None,
    yes_bid: Optional[float] = None,
    yes_ask: Optional[float] = None,
    volume_24h: Optional[float] = None,
    open_interest: Optional[float] = None,
    api_key: Optional[str] = None,
    private_key=None,
    sleep_between_calls: float = 0.5,
) -> dict:
    """Collect a single market snapshot (orderbook + trades + events).

    Args:
        ticker: Market ticker string
        question: Human-readable question (optional)
        yes_bid, yes_ask: Current best prices (optional, for spread calc)
        volume_24h: 24h volume (optional, for spike detection)
        open_interest: Open interest (optional, for context)
        api_key, private_key: Kalshi credentials (loaded from env if None)
        sleep_between_calls: Seconds to sleep between API calls

    Returns:
        Structured snapshot dict
    """
    if api_key is None or private_key is None:
        api_key, private_key = _load_credentials()
        if api_key is None:
            logger.error("Cannot collect snapshot without Kalshi credentials")
            return {}

    # Fetch orderbook
    orderbook_raw = fetch_orderbook(ticker, api_key, private_key)
    if sleep_between_calls > 0:
        time.sleep(sleep_between_calls)

    # Fetch trades
    trades_raw = fetch_trades(ticker, api_key, private_key, limit=100)

    # Parse
    orderbook = _parse_orderbook(orderbook_raw)
    recent_trades = _parse_trades(trades_raw)

    # Calculate spread from orderbook if not provided
    if yes_bid is None and orderbook["yes_bids"]:
        yes_bid = orderbook["yes_bids"][0][0]
    if yes_ask is None and orderbook["no_bids"]:
        # yes_ask = 1.0 - no_bid (since no_bids are the other side)
        yes_ask = 1.0 - orderbook["no_bids"][0][0]

    spread_cents = None
    if yes_bid is not None and yes_ask is not None and yes_ask >= yes_bid:
        spread_cents = yes_ask - yes_bid

    # Detect events
    events = _detect_events(
        yes_bid, yes_ask, volume_24h, open_interest,
        orderbook, recent_trades,
    )

    snapshot = {
        "timestamp_utc": _now_utc_iso(),
        "venue": "kalshi",
        "ticker": ticker,
        "question": question or ticker,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "spread_cents": spread_cents,
        "volume_24h": volume_24h,
        "open_interest": open_interest,
        "orderbook": orderbook,
        "recent_trades": recent_trades,
        "events": events,
    }

    logger.info(
        f"[ORDERBOOK_SIGNAL] {ticker}: "
        f"yes_bid={yes_bid}, yes_ask={yes_ask}, "
        f"spread={spread_cents}, events={len(events)}"
    )

    return snapshot
