#!/usr/bin/env python3
"""
Label Resolver — XGBoost Training Pipeline

Checks for settled markets and fills in outcome + settlement_price
in market_observations for any observations where the market has resolved.

Usage:
  python3 -m ml.label_resolver              # resolve all pending observations
  python3 -m ml.label_resolver --dry-run    # show what would be labeled
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BUNDLE_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
sys.path.insert(0, str(BUNDLE_DIR))

DB_PATH = BUNDLE_DIR / "ml" / "training_data.db"
CACHE_FILE = BUNDLE_DIR / "data" / "ml_label_cache.json"

KALSHI_API_KEY = os.getenv("KALSHI_KEY", "d1ac170e-5bb1-4d6a-b483-a2f76e072c7a")
KALSHI_SECRET_FILE = os.getenv("KALSHI_SECRET_FILE", str(BUNDLE_DIR / "keys" / "kalshi-prod.key"))
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
API_DELAY_SEC = 0.05


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


def resolve_market(market_id: str) -> dict | None:
    """Query Kalshi API for market resolution status."""
    import requests

    path = f"/markets/{market_id}"
    headers = _get_kalshi_headers("GET", path)

    try:
        resp = requests.get(f"{KALSHI_BASE_URL}{path}", headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        market = data.get("market", {})
        return {
            "status": market.get("status"),
            "result": market.get("result"),
            "settlement_price": market.get("settlement_price"),
            "close_time": market.get("close_time"),
        }
    except Exception:
        return None


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_unlabeled_tickers(conn: sqlite3.Connection) -> list[str]:
    """Get unique tickers that have observations but no labels."""
    rows = conn.execute(
        """SELECT DISTINCT ticker FROM market_observations
           WHERE outcome IS NULL"""
    ).fetchall()
    return [r["ticker"] for r in rows]


def resolve_and_label(dry_run: bool = False, verbose: bool = False) -> int:
    """
    Resolve all unlabeled markets and fill in their labels.
    Returns number of newly labeled observations.
    """
    cache = load_cache()
    conn = get_db()

    # Get all tickers that need resolution
    pending_tickers = get_unlabeled_tickers(conn)
    if not pending_tickers:
        print("[INFO] No pending markets to resolve.", file=sys.stderr)
        return 0

    print(f"[INFO] Resolving {len(pending_tickers)} pending markets...", file=sys.stderr)

    labeled_count = 0
    newly_resolved = 0

    for i, ticker in enumerate(pending_tickers):
        if ticker in cache:
            res = cache[ticker]
        else:
            res = resolve_market(ticker)
            cache[ticker] = res
            if (i + 1) % 100 == 0:
                save_cache(cache)

        if verbose and i % 50 == 0:
            print(f"[DEBUG] Resolved {i+1}/{len(pending_tickers)}...", file=sys.stderr)

        time.sleep(API_DELAY_SEC)

        if res is None:
            continue

        status = res.get("status", "")
        result = res.get("result")

        # Market is settled
        if status == "finalized" and result in ("yes", "no"):
            newly_resolved += 1
            outcome = 1 if result == "yes" else 0
            settlement_price = res.get("settlement_price")
            if settlement_price is None:
                settlement_price = 1.0 if result == "yes" else 0.0

            if dry_run:
                print(f"[DRY RUN] Would label {ticker}: outcome={outcome}, settlement={settlement_price}", file=sys.stderr)
            else:
                conn.execute(
                    """UPDATE market_observations
                       SET outcome=?, settlement_price=?
                       WHERE ticker=? AND outcome IS NULL""",
                    (outcome, settlement_price, ticker),
                )

            labeled_count += 1

    if not dry_run:
        conn.commit()
        save_cache(cache)

    print(f"[INFO] Labeled {labeled_count} observations from {newly_resolved} newly resolved markets", file=sys.stderr)
    conn.close()
    return labeled_count


def main():
    parser = argparse.ArgumentParser(description="Label Resolver for ML Training Data")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be labeled")
    parser.add_argument("--verbose", action="store_true", help="Debug output")
    args = parser.parse_args()

    n = resolve_and_label(dry_run=args.dry_run, verbose=args.verbose)
    if args.dry_run:
        print(f"[DRY RUN] Would have labeled {n} observations")
    else:
        print(f"[DONE] Labeled {n} observations")


if __name__ == "__main__":
    main()
