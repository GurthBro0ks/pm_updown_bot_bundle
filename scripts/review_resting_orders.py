#!/usr/bin/env python3
"""
Resting Order Review Script — Read-only review of open orders.
Does NOT cancel or modify any orders.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/slimy/pm_updown_bot_bundle")

from utils.kalshi_orders import KalshiOrderClient

# Category mapping (same as kalshi_optimize.py)
_TICKER_CATEGORY_MAP = {
    "KXINX": "index", "KXINXU": "index", "KXNDX": "index",
    "KXNASDAQ100": "index", "KXNASDAQ100U": "index",
    "KXBTC": "crypto", "KXETH": "crypto", "KXETHY": "crypto",
    "KXMVESPORTS": "sports", "KXBUNDESLIGA": "sports",
    "KXGOV": "politics", "KXECON": "economics",
    "KXCOACH": "sports", "KXNFL": "sports",
}


def _extract_category(ticker: str) -> str:
    prefix = ticker.split("-")[0] if "-" in ticker else ticker[:12]
    for key, cat in _TICKER_CATEGORY_MAP.items():
        if prefix.startswith(key):
            return cat
    if "SPORTS" in ticker or "NFL" in ticker or "NBA" in ticker:
        return "sports"
    if "INX" in ticker or "NASDAQ" in ticker:
        return "index"
    if "BTC" in ticker or "ETH" in ticker:
        return "crypto"
    return "other"


def main():
    print("=" * 60)
    print("RESTING ORDER REVIEW REPORT")
    print("=" * 60)
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    print()

    client = KalshiOrderClient()
    orders = client.get_orders(status="resting") or []
    positions = client.get_positions() or []

    print(f"Resting orders: {len(orders)}")
    print(f"Open positions: {len(positions)}")
    print()

    if not orders:
        print("No resting orders to review.")
        return

    now = datetime.now(timezone.utc)

    # Categorize orders
    stale_orders = []
    long_expiry_orders = []
    blocked_category_orders = []
    cheap_orders = []
    duplicate_orders = {}

    for o in orders:
        ticker = o.get("ticker", "?")
        created_str = o.get("created_time", "")
        yes_price = o.get("yes_price_dollars", "0")
        try:
            price = float(yes_price)
        except (ValueError, TypeError):
            price = 0.0

        # Age
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            age_hours = (now - created).total_seconds() / 3600
        except Exception:
            age_hours = 0

        # Category
        category = _extract_category(ticker)

        # Stale (>24h)
        if age_hours > 24:
            stale_orders.append(o)

        # Long expiry (>14d) - rough check from ticker
        if "27JAN" in ticker or "26DEC" in ticker or "26JUL" in ticker or "26JUN" in ticker:
            long_expiry_orders.append(o)

        # Blocked category
        if category in ("sports", "esports", "other"):
            blocked_category_orders.append(o)

        # Cheap (<5c)
        if price < 0.05:
            cheap_orders.append(o)

        # Duplicates
        duplicate_orders.setdefault(ticker, []).append(o)

    # Report sections
    print("## Summary")
    print(f"- Stale (>24h): {len(stale_orders)}")
    print(f"- Long expiry (>14d): {len(long_expiry_orders)}")
    print(f"- Blocked category: {len(blocked_category_orders)}")
    print(f"- Cheap (<5c): {len(cheap_orders)}")
    print(f"- Duplicate tickers: {sum(1 for v in duplicate_orders.values() if len(v) > 1)}")
    print()

    if stale_orders:
        print("## Stale Orders (>24h old)")
        print("| Ticker | Created | Age (h) | Price |")
        print("|--------|---------|---------|-------|")
        for o in stale_orders:
            ticker = o.get("ticker", "?")
            created = o.get("created_time", "")[:10]
            try:
                age = (now - datetime.fromisoformat(o.get("created_time", "").replace("Z", "+00:00"))).total_seconds() / 3600
            except Exception:
                age = 0
            price = o.get("yes_price_dollars", "?")
            print(f"| {ticker} | {created} | {age:.1f} | {price} |")
        print()

    if long_expiry_orders:
        print("## Long Expiry Orders (>14d)")
        print("| Ticker | Created | Price |")
        print("|--------|---------|-------|")
        for o in long_expiry_orders:
            ticker = o.get("ticker", "?")
            created = o.get("created_time", "")[:10]
            price = o.get("yes_price_dollars", "?")
            print(f"| {ticker} | {created} | {price} |")
        print()

    if blocked_category_orders:
        print("## Blocked Category Orders")
        print("| Ticker | Category | Price |")
        print("|--------|----------|-------|")
        for o in blocked_category_orders:
            ticker = o.get("ticker", "?")
            cat = _extract_category(ticker)
            price = o.get("yes_price_dollars", "?")
            print(f"| {ticker} | {cat} | {price} |")
        print()

    if cheap_orders:
        print("## Cheap Orders (<5c)")
        print("| Ticker | Price |")
        print("|--------|-------|")
        for o in cheap_orders:
            ticker = o.get("ticker", "?")
            price = o.get("yes_price_dollars", "?")
            print(f"| {ticker} | {price} |")
        print()

    duplicates = {k: v for k, v in duplicate_orders.items() if len(v) > 1}
    if duplicates:
        print("## Duplicate Tickers")
        for ticker, orders_list in duplicates.items():
            print(f"- {ticker}: {len(orders_list)} orders")
        print()

    print("## Recommendation")
    print("Consider reviewing the following for manual cancellation:")
    if stale_orders:
        print(f"- {len(stale_orders)} stale orders (>24h old, may never fill)")
    if long_expiry_orders:
        print(f"- {len(long_expiry_orders)} long-dated orders (ties up capital)")
    if blocked_category_orders:
        print(f"- {len(blocked_category_orders)} blocked-category orders (should not have been placed)")
    if cheap_orders:
        print(f"- {len(cheap_orders)} cheap orders (<5c, high risk)")
    print()
    print("**Note:** This script is read-only. No orders were cancelled.")


if __name__ == "__main__":
    main()
