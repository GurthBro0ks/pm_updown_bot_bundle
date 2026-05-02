"""
Kalshi Weather Market Discovery Module

Discovers open KXHIGH (daily high temperature) markets on Kalshi across supported cities.
Parses ticker format to extract city, date, and temperature threshold/bin.

Supported cities: NYC, Chicago, Miami, LA, Denver
"""

import os
import re
import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone, timedelta

import requests
from cryptography.hazmat.primitives import serialization

from utils.kalshi import get_kalshi_headers

logger = logging.getLogger(__name__)

# Kalshi KXHIGH series tickers we monitor
WEATHER_SERIES_TICKERS = [
    "KXHIGHNY",
    "KXHIGHCHI",
    "KXHIGHMIA",
    "KXHIGHLAX",
    "KXHIGHDEN",
]

# Map series ticker to city code
SERIES_TO_CITY = {
    "KXHIGHNY": "NY",
    "KXHIGHCHI": "CHI",
    "KXHIGHMIA": "MIA",
    "KXHIGHLAX": "LAX",
    "KXHIGHDEN": "DEN",
}

# City code to full name
CITY_NAMES = {
    "NY": "NYC",
    "CHI": "Chicago",
    "MIA": "Miami",
    "LAX": "LA",
    "DEN": "Denver",
}

KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def _parse_kxhigh_ticker(ticker: str) -> Optional[Dict]:
    """
    Parse a KXHIGH ticker to extract metadata.

    Examples:
        KXHIGHNY-26MAY02-B85    → above 85°F
        KXHIGHCHI-26MAY02-B82   → above 82°F
        KXHIGHNY-26MAY02-B85.5  → above 85.5°F

    Returns dict with:
        city_code: str (NY, CHI, MIA, LAX, DEN)
        city_name: str
        date_str: str (e.g., "26MAY02")
        threshold: float (temperature threshold)
        comparison: str ("above" for KXHIGH markets)
    """
    result = {
        "city_code": None,
        "city_name": None,
        "date_str": None,
        "threshold": None,
        "comparison": "above",
    }

    # Try to extract series/city from prefix
    for series, city_code in SERIES_TO_CITY.items():
        if ticker.startswith(series):
            result["city_code"] = city_code
            result["city_name"] = CITY_NAMES.get(city_code)
            break

    if not result["city_code"]:
        return None

    # Parse date and threshold from remaining parts
    # Format: KXHIGHNY-26MAY02-B85 or KXHIGHNY-26MAY02-B85.5
    parts = ticker.split("-")
    if len(parts) >= 2:
        result["date_str"] = parts[1]

    if len(parts) >= 3:
        threshold_part = parts[2]
        # Remove 'B' prefix (B = above/buy YES)
        if threshold_part.startswith("B"):
            threshold_str = threshold_part[1:]
            try:
                result["threshold"] = float(threshold_str)
            except ValueError:
                pass

    if result["threshold"] is None:
        return None

    return result


def _get_auth() -> tuple:
    """Get Kalshi API key and private key for auth."""
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv("KALSHI_SECRET_FILE", "./kalshi_private_key.pem")

    if not api_key:
        logger.error("[WEATHER_MARKETS] KALSHI_KEY not set")
        return None, None

    try:
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    except Exception as e:
        logger.error(f"[WEATHER_MARKETS] Failed to load private key: {e}")
        return None, None

    return api_key, private_key


def fetch_markets_for_series(series_ticker: str, api_key: str, private_key) -> List[Dict]:
    """
    Fetch open markets for a specific weather series.

    Returns list of market dicts with:
        ticker, title, yes_bid, yes_ask, mid_price, volume,
        close_time, city_code, city_name, date_str, threshold
    """
    headers = get_kalshi_headers('GET', '/markets', api_key, private_key)

    try:
        resp = requests.get(
            f"{KALSHI_BASE_URL}/markets",
            headers=headers,
            params={"status": "open", "series_ticker": series_ticker, "limit": 100},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(
                f"[WEATHER_MARKETS] Series {series_ticker} API error: {resp.status_code}"
            )
            return []

        data = resp.json()
        markets = data.get("markets", [])
    except Exception as e:
        logger.error(f"[WEATHER_MARKETS] Failed to fetch {series_ticker}: {e}")
        return []

    results = []
    for m in markets:
        ticker = m.get("ticker", "")
        parsed = _parse_kxhigh_ticker(ticker)
        if not parsed:
            continue

        yes_bid = m.get("yes_bid", 0)
        yes_ask = m.get("yes_ask", 0)

        # Convert from cents to dollars if needed
        if yes_ask > 1:
            yes_bid = yes_bid / 100.0
            yes_ask = yes_ask / 100.0

        mid_price = (yes_bid + yes_ask) / 2.0 if yes_bid and yes_ask else (yes_bid or yes_ask or 0.5)

        # Calculate spread in cents
        spread_cents = (yes_ask - yes_bid) * 100 if yes_ask and yes_bid else 999

        close_time = m.get("close_time") or m.get("expiration_time", "")

        # Calculate hours to close
        hours_to_close = 999
        if close_time:
            try:
                # Parse ISO format
                close_dt = datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                hours_to_close = (close_dt - now).total_seconds() / 3600
            except Exception:
                pass

        results.append({
            "ticker": ticker,
            "title": m.get("title", m.get("short_name", "")),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "mid_price": mid_price,
            "volume": m.get("volume", 0),
            "open_interest": m.get("open_interest", 0),
            "close_time": close_time,
            "hours_to_close": hours_to_close,
            "spread_cents": spread_cents,
            "city_code": parsed["city_code"],
            "city_name": parsed["city_name"],
            "date_str": parsed["date_str"],
            "threshold": parsed["threshold"],
            "comparison": parsed["comparison"],
        })

    return results


def discover_weather_markets(max_hours_to_close: float = 48.0) -> List[Dict]:
    """
    Discover all open KXHIGH weather markets across supported cities.

    Args:
        max_hours_to_close: Only include markets closing within this many hours (default 48)

    Returns:
        List of market dicts with full metadata
    """
    api_key, private_key = _get_auth()
    if not api_key:
        logger.error("[WEATHER_MARKETS] Cannot discover markets: auth failed")
        return []

    all_markets = []
    for series_ticker in WEATHER_SERIES_TICKERS:
        markets = fetch_markets_for_series(series_ticker, api_key, private_key)
        all_markets.extend(markets)

    # Filter to markets closing soon enough
    filtered = [m for m in all_markets if m["hours_to_close"] <= max_hours_to_close]

    # Sort by volume (highest first)
    filtered.sort(key=lambda m: m["volume"], reverse=True)

    logger.info(
        f"[WEATHER_MARKETS] Discovered {len(all_markets)} total KXHIGH markets, "
        f"{len(filtered)} within {max_hours_to_close}h close window"
    )

    return filtered


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    markets = discover_weather_markets()
    print(f"\nFound {len(markets)} tradeable weather markets:")
    for m in markets[:10]:
        print(
            f"  {m['ticker']}: {m['city_name']} above {m['threshold']}°F "
            f"| bid={m['yes_bid']:.2f} ask={m['yes_ask']:.2f} "
            f"| vol={m['volume']} | close in {m['hours_to_close']:.0f}h"
        )
