#!/usr/bin/env python3
"""
Shadow Trade Resolver

Parses shadow-mode trade logs, resolves each market via Kalshi API,
calculates what the PnL would have been, and writes resolved trades
into paper_trading/pnl.db so bootstrap_validator has enough data.

Usage:
  python3 shadow_resolver.py                    # full run (resumes if cache exists)
  python3 shadow_resolver.py --dry-run          # parse + resolve, don't write
  python3 shadow_resolver.py --log-dir ./logs   # custom log path
  python3 shadow_resolver.py --verbose          # debug output
  python3 shadow_resolver.py --force            # clear cache and restart resolution
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Paths
BUNDLE_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
LOG_DIR = BUNDLE_DIR / "logs"
DB_PATH = BUNDLE_DIR / "paper_trading" / "pnl.db"
PROOFS_DIR = BUNDLE_DIR / "proofs"
CACHE_FILE = BUNDLE_DIR / "data" / "shadow_resolution_cache.json"

# Kalshi API
KALSHI_API_KEY = os.getenv("KALSHI_KEY", "d1ac170e-5bb1-4d6a-b483-a2f76e072c7a")
KALSHI_SECRET_FILE = os.getenv("KALSHI_SECRET_FILE", str(BUNDLE_DIR / "keys" / "kalshi-prod.key"))
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Fees
KALSHI_FEE_PCT = 0.07  # Kalshi fee multiplier

# Rate limiting
API_DELAY_SEC = 0.05  # delay between API calls


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

# Regex: "SHADOW MODE: Would place order on {market_id}: {side} ${size} @ {price}"
SHADOW_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[,\d]*)\s+\|.*?SHADOW MODE: Would place order on "
    r"([KXMVESPNOA-Z0-9_-]+):\s+(yes|no)\s+\$([\d.]+)\s+@\s+([\d.]+)"
)


def parse_shadow_trades_from_logs(log_dir: Path, verbose: bool = False) -> list[dict]:
    """Scan all .log files in log_dir for shadow trade entries."""
    trades = []
    seen = set()

    for log_file in sorted(log_dir.glob("*.log")):
        try:
            content = log_file.read_text(errors="ignore")
        except Exception:
            continue

        for line in content.splitlines():
            m = SHADOW_RE.search(line)
            if not m:
                continue

            ts_str, market_id, side, size_str, price_str = m.groups()

            # Normalize timestamp
            ts_str = ts_str.replace(",", "T")
            try:
                ts_str = ts_str.split(".")[0]
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
                ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                ts = datetime.now(timezone.utc)

            key = (market_id, ts.isoformat())
            if key in seen:
                continue
            seen.add(key)

            trades.append({
                "market_id": market_id,
                "side": side.strip().lower(),
                "size_usd": float(size_str),
                "entry_price": float(price_str),
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "phase": "shadow",
            })

    return trades


# ---------------------------------------------------------------------------
# Kalshi API
# ---------------------------------------------------------------------------

def _get_kalshi_headers(method: str, path: str) -> dict:
    """Generate Kalshi signed headers."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes
    import base64
    import time as _time

    with open(KALSHI_SECRET_FILE, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    timestamp = str(int(_time.time()))
    msg = f"{timestamp}{method}/trade-api/v2{path}"
    signature = private_key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


def resolve_market(market_id: str) -> Optional[dict]:
    """
    Query Kalshi API for a single market's resolution status.
    Returns dict with keys: status, result, title, close_time
    Returns None if market cannot be resolved (404, network error, etc).
    """
    import requests

    path = f"/markets/{market_id}"
    headers = _get_kalshi_headers("GET", path)

    try:
        resp = requests.get(f"{KALSHI_BASE_URL}{path}", headers=headers, timeout=10)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            return None

        data = resp.json()
        market = data.get("market", {})
        return {
            "status": market.get("status"),
            "result": market.get("result"),
            "title": market.get("title", ""),
            "close_time": market.get("close_time"),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def load_cache(cache_file: Path) -> dict:
    """Load resolution cache from disk."""
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass
    return {"resolutions": {}, "resolved_count": 0, "total_count": 0}


def save_cache(cache_file: Path, cache: dict):
    """Save resolution cache to disk."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache))


def resolve_markets_with_cache(
    unique_markets: list[str],
    cache_file: Path,
    force_restart: bool = False,
    verbose: bool = False,
) -> dict[str, Optional[dict]]:
    """Resolve markets with caching for resumability."""
    cache = load_cache(cache_file)

    if force_restart:
        cache = {"resolutions": {}, "resolved_count": 0, "total_count": len(unique_markets)}

    cache["total_count"] = len(unique_markets)
    resolutions: dict[str, Optional[dict]] = dict(cache["resolutions"])

    # Markets still to resolve
    pending = [m for m in unique_markets if m not in resolutions]

    if not pending:
        print(f"[INFO] All {len(unique_markets)} markets already cached.", file=sys.stderr)
        return resolutions

    print(f"[INFO] Resolving {len(pending)} pending markets (cache has {len(resolutions)} resolved)...", file=sys.stderr)

    for i, market_id in enumerate(pending):
        if verbose and i % 50 == 0:
            print(f"[DEBUG] Resolving market {i+1}/{len(pending)}: {market_id}", file=sys.stderr)

        resolution = resolve_market(market_id)
        resolutions[market_id] = resolution
        cache["resolutions"][market_id] = resolution

        # Save checkpoint every 200 markets
        if (i + 1) % 200 == 0:
            cache["resolved_count"] = len(resolutions)
            save_cache(cache_file, cache)
            elapsed = (i + 1) / (time.time() - _start_time) if '_start_time' in globals() else 0
            print(f"[INFO] Progress: {len(resolutions)}/{len(unique_markets)} markets resolved...", file=sys.stderr)

        time.sleep(API_DELAY_SEC)

    cache["resolved_count"] = len(resolutions)
    save_cache(cache_file, cache)
    return resolutions


# ---------------------------------------------------------------------------
# PnL calculation
# ---------------------------------------------------------------------------

def calculate_pnl(side: str, entry_price: float, size_usd: float, result: str) -> tuple[float, float]:
    """
    Calculate PnL for a shadow trade.

    Fee: kalshi_fee_pct * entry_price * (1 - entry_price) * size_usd
    (Kalshi's quadratic fee on the YES position value)

    For YES buy:
      - resolved YES: pnl = (1.00 - entry_price) * size_usd - fee
      - resolved NO:  pnl = -(entry_price * size_usd) - fee

    For NO buy:
      - resolved NO:  pnl = entry_price * size_usd - fee
      - resolved YES: pnl = -(1.00 - entry_price) * size_usd - fee
    """
    fee = KALSHI_FEE_PCT * entry_price * (1 - entry_price) * size_usd

    if side == "yes":
        if result == "yes":
            pnl = (1.0 - entry_price) * size_usd - fee
        else:
            pnl = -(entry_price * size_usd) - fee
    else:
        if result == "no":
            pnl = entry_price * size_usd - fee
        else:
            pnl = -(1.0 - entry_price) * size_usd - fee

    pnl_pct = (pnl / size_usd) * 100.0 if size_usd > 0 else 0.0
    return (round(pnl, 6), round(pnl_pct, 4))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_existing_trade_keys(db_path: Path) -> set:
    """Return set of (market_id, timestamp) pairs already in DB."""
    existing = set()
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT ticker, timestamp FROM trades WHERE action='EXIT'").fetchall()
        conn.close()
        for r in rows:
            existing.add((r["ticker"], r["timestamp"]))
    except Exception:
        pass
    return existing


def write_shadow_trades(db_path: Path, resolved_trades: list[dict], dry_run: bool = False) -> int:
    """Write resolved shadow trades to DB. Returns number of rows inserted."""
    if not resolved_trades:
        return 0

    inserted = 0
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        for trade in resolved_trades:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO trades
                        (phase, ticker, action, price, size_usd, pnl_usd, pnl_pct, timestamp)
                    VALUES (?, ?, 'EXIT', ?, ?, ?, ?, ?)
                """, (
                    trade["phase"],
                    trade["market_id"],
                    trade["exit_price"],
                    trade["size_usd"],
                    trade["pnl_usd"],
                    trade["pnl_pct"],
                    trade["timestamp"],
                ))
                inserted += 1
            except Exception:
                pass

        if not dry_run:
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR] DB write failed: {e}", file=sys.stderr)

    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _start_time
    _start_time = time.time()

    parser = argparse.ArgumentParser(description="Shadow Trade Resolver")
    parser.add_argument("--dry-run", action="store_true", help="Parse + resolve, don't write to DB")
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR, help="Path to log directory")
    parser.add_argument("--verbose", action="store_true", help="Print debug output")
    parser.add_argument("--force", action="store_true", help="Clear cache and restart resolution")
    args = parser.parse_args()

    log_dir: Path = args.log_dir
    dry_run: bool = args.dry_run
    verbose: bool = args.verbose
    force: bool = args.force

    # -------------------------------------------------------------------------
    # 1. Parse shadow trades from logs
    # -------------------------------------------------------------------------
    print(f"[INFO] Scanning {log_dir} for shadow trades...", file=sys.stderr)
    shadow_trades = parse_shadow_trades_from_logs(log_dir, verbose=verbose)
    print(f"[INFO] Shadow trades parsed: {len(shadow_trades)}", file=sys.stderr)

    if not shadow_trades:
        print("[ERROR] No shadow trades found in logs", file=sys.stderr)
        return

    # Deduplicate by (market_id, timestamp)
    unique_trades = {}
    for t in shadow_trades:
        key = (t["market_id"], t["timestamp"])
        if key not in unique_trades:
            unique_trades[key] = t
    shadow_trades = list(unique_trades.values())
    print(f"[INFO] After deduplication: {len(shadow_trades)}", file=sys.stderr)

    # -------------------------------------------------------------------------
    # 2. Load existing DB trades
    # -------------------------------------------------------------------------
    existing_keys = get_existing_trade_keys(DB_PATH)
    print(f"[INFO] Already in DB (skipped): {len(existing_keys)}", file=sys.stderr)

    # Filter out already-in-DB trades
    shadow_trades = [t for t in shadow_trades if (t["market_id"], t["timestamp"]) not in existing_keys]
    print(f"[INFO] After removing existing: {len(shadow_trades)}", file=sys.stderr)

    if not shadow_trades:
        print("[INFO] No new shadow trades to resolve.", file=sys.stderr)
        return

    # -------------------------------------------------------------------------
    # 3. Resolve markets via Kalshi API (with caching)
    # -------------------------------------------------------------------------
    unique_markets = list({t["market_id"] for t in shadow_trades})
    print(f"[INFO] Unique markets to resolve: {len(unique_markets)}", file=sys.stderr)

    resolutions = resolve_markets_with_cache(
        unique_markets,
        CACHE_FILE,
        force_restart=force,
        verbose=verbose,
    )

    # -------------------------------------------------------------------------
    # 4. Calculate PnL for each trade
    # -------------------------------------------------------------------------
    resolved_trades_out: list[dict] = []
    total_resolved = 0
    total_unresolvable = 0

    for t in shadow_trades:
        market_id = t["market_id"]
        res = resolutions.get(market_id)

        if res is None:
            total_unresolvable += 1
            continue
        if not res.get("result"):
            total_unresolvable += 1
            continue
        if res.get("result") not in ("yes", "no"):
            total_unresolvable += 1
            continue

        total_resolved += 1
        result = res["result"]
        entry_price = t["entry_price"]
        size_usd = t["size_usd"]
        side = t["side"]

        pnl_usd, pnl_pct = calculate_pnl(side, entry_price, size_usd, result)
        exit_price = 1.0 if result == "yes" else 0.0

        resolved_trades_out.append({
            "market_id": market_id,
            "phase": "shadow",
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size_usd": size_usd,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "timestamp": t["timestamp"],
            "result": result,
        })

    print(f"[INFO] Resolution checked: {len(unique_markets)}", file=sys.stderr)
    print(f"[INFO] Resolved (has result): {total_resolved}", file=sys.stderr)
    print(f"[INFO] Unresolved/skipped: {total_unresolvable}", file=sys.stderr)
    print(f"[INFO] Trades with calculable PnL: {len(resolved_trades_out)}", file=sys.stderr)

    if not resolved_trades_out:
        print("[INFO] No resolvable shadow trades to write.", file=sys.stderr)
        return

    # -------------------------------------------------------------------------
    # 5. Write to DB
    # -------------------------------------------------------------------------
    if dry_run:
        print(f"[DRY RUN] Would insert {len(resolved_trades_out)} trades:", file=sys.stderr)
        for t in resolved_trades_out[:5]:
            print(f"  {t['market_id'][:50]}: {t['side']} @ {t['entry_price']} → {t['result']} | pnl=${t['pnl_usd']:.4f}", file=sys.stderr)
        if len(resolved_trades_out) > 5:
            print(f"  ... and {len(resolved_trades_out) - 5} more", file=sys.stderr)
    else:
        inserted = write_shadow_trades(DB_PATH, resolved_trades_out)
        print(f"[INFO] New trades written to DB: {inserted}", file=sys.stderr)

    # -------------------------------------------------------------------------
    # 6. Summary
    # -------------------------------------------------------------------------
    wins = sum(1 for t in resolved_trades_out if t["pnl_usd"] > 0)
    total_pnl = sum(t["pnl_usd"] for t in resolved_trades_out)
    win_rate = (wins / len(resolved_trades_out) * 100) if resolved_trades_out else 0

    sep = "=" * 60
    print()
    print(sep)
    print("SHADOW TRADE RESOLVER".center(60))
    print(sep)
    print(f"  Log files scanned:       {len(list(log_dir.glob('*.log')))}")
    print(f"  Shadow trades parsed:    {len(shadow_trades) + len(existing_keys)} (total in logs)")
    print(f"  Already in DB:          {len(existing_keys)}")
    print(f"  Unique markets checked: {len(unique_markets)}")
    print(f"  Resolved (has result): {total_resolved}")
    print(f"  Unresolved/skipped:    {total_unresolvable}")
    print(f"  New trades written:   {len(resolved_trades_out)}")
    print(f"  Total PnL (shadow):   ${total_pnl:.4f}")
    print(f"  Win rate (shadow):    {win_rate:.1f}%")
    print()
    print(f"  → Run bootstrap_validator.py to validate edge")
    print(sep)


if __name__ == "__main__":
    main()
