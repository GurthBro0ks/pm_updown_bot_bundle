#!/usr/bin/env python3
"""
VPIN (Volume-synchronized Probability of Informed Trading) module.

VPIN detects informed trading activity in prediction markets by measuring
the imbalance between buy and sell volumes bucketed by trade volume.

VPIN Gate Decision:
  - action="halt"  : VPIN > halt threshold (0.80) — skip market entirely
  - action="widen" : VPIN > warn threshold (0.65) — double spread requirement
  - action="allow" : VPIN within normal range — proceed normally

Slots in as a PRE-GATE filter before check_micro_live_gates().
"""

import argparse
import logging
import numpy as np
from typing import Optional

logger = logging.getLogger("vpin")

# Default thresholds
DEFAULT_THRESHOLDS = {"warn": 0.65, "halt": 0.80}


def compute_vpin(buy_volumes: list, sell_volumes: list, bucket_size: int = 50) -> np.ndarray:
    """
    Compute VPIN (Volume-synchronized Probability of Informed Trading).

    Buckets trades into volume bars of bucket_size, then calculates
    the rolling mean of |buy_vol - sell_vol| / (buy_vol + sell_vol).

    Args:
        buy_volumes:  List of buy trade volumes
        sell_volumes: List of sell trade volumes
        bucket_size:  Volume per bucket (default 50)

    Returns:
        np.ndarray of per-bucket VPIN values
    """
    buy_vol = np.array(buy_volumes, dtype=float)
    sell_vol = np.array(sell_volumes, dtype=float)

    if len(buy_vol) == 0 and len(sell_vol) == 0:
        return np.array([])

    # Pad to equal length
    n = max(len(buy_vol), len(sell_vol))
    buy_vol = np.pad(buy_vol, (0, n - len(buy_vol)), constant_values=0)
    sell_vol = np.pad(sell_vol, (0, n - len(sell_vol)), constant_values=0)

    total_vol = buy_vol + sell_vol
    vpin_bar = np.abs(buy_vol - sell_vol) / np.where(total_vol > 0, total_vol, 1.0)

    # Volume-bucket the bars
    cumulative_vol = np.cumsum(total_vol)
    bucket_edges = np.arange(bucket_size, cumulative_vol[-1] + bucket_size, bucket_size)

    if len(bucket_edges) == 0:
        return vpin_bar  # Single bucket

    bucket_starts = np.searchsorted(cumulative_vol, bucket_edges[:-1], side="left")
    bucket_ends = np.searchsorted(cumulative_vol, bucket_edges, side="right")

    vpin_values = []
    for start, end in zip(bucket_starts, bucket_ends):
        if end > start:
            vpin_values.append(np.mean(vpin_bar[start:end]))

    if not vpin_values:
        return np.array([np.mean(vpin_bar)]) if len(vpin_bar) > 0 else np.array([])

    return np.array(vpin_values)


def get_vpin_gate_decision(vpin_value: float, thresholds: dict = None) -> dict:
    """
    Decide trade action based on VPIN value.

    Args:
        vpin_value:   Current VPIN value (0-1)
        thresholds:   Dict with "warn" and "halt" keys

    Returns:
        dict: {"action": "allow"|"widen"|"halt", "vpin": float, "bucket_count": int}
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    warn = thresholds.get("warn", 0.65)
    halt = thresholds.get("halt", 0.80)

    if vpin_value >= halt:
        action = "halt"
    elif vpin_value >= warn:
        action = "widen"
    else:
        action = "allow"

    return {"action": action, "vpin": float(vpin_value), "bucket_count": 0}


def get_market_vpin(market_id: str) -> dict:
    """
    Wrapper that pulls recent trade tape for a market and computes VPIN.

    Uses tick rule for buy/sell classification: price up = buy, price down = sell.

    Args:
        market_id: Kalshi market ID

    Returns:
        dict: {"action": "allow"|"widen"|"halt", "vpin": float,
               "bucket_count": int, "reason": str}
    """
    try:
        trades = _fetch_trade_tape(market_id)
        if trades is None or len(trades) < 2:
            return {
                "action": "allow",
                "vpin": 0.0,
                "bucket_count": 0,
                "reason": "no_data",
            }

        buy_volumes = []
        sell_volumes = []

        for i, trade in enumerate(trades):
            if i == 0:
                continue
            price_change = trade.get("price", 0) - trades[i - 1].get("price", 0)
            volume = trade.get("volume", 1)
            if price_change > 0:
                buy_volumes.append(volume)
            elif price_change < 0:
                sell_volumes.append(volume)
            else:
                if i > 1 and trades[i - 1].get("price", 0) >= trades[i - 2].get("price", 0):
                    buy_volumes.append(volume)
                else:
                    sell_volumes.append(volume)

        vpin_bars = compute_vpin(buy_volumes, sell_volumes)

        if len(vpin_bars) == 0:
            return {
                "action": "allow",
                "vpin": 0.0,
                "bucket_count": 0,
                "reason": "insufficient_trades",
            }

        current_vpin = float(vpin_bars[-1])
        decision = get_vpin_gate_decision(current_vpin)
        decision["bucket_count"] = len(vpin_bars)
        decision["reason"] = "computed"

        logger.debug(
            f"[VPIN] Market {market_id}: vpin={current_vpin:.3f} action={decision['action']} "
            f"buckets={len(vpin_bars)}"
        )
        return decision

    except Exception as e:
        logger.debug(f"[VPIN] Market {market_id}: exception={e}, allowing on fallback")
        return {
            "action": "allow",
            "vpin": 0.0,
            "bucket_count": 0,
            "reason": "error",
        }


def _fetch_trade_tape(market_id: str) -> Optional[list]:
    """
    Fetch recent trade tape for a market.

    Returns list of {"price": float, "volume": float, "timestamp": str} or None if unavailable.
    """
    try:
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "paper_trading" / "pnl.db"
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT timestamp, side, price, size FROM trades
                WHERE market_id = ? OR ticker = ?
                ORDER BY timestamp DESC
                LIMIT 200
                """,
                (market_id, market_id),
            )
            rows = cur.fetchall()
            conn.close()

            if rows:
                trades = [
                    {
                        "price": float(r["price"]),
                        "volume": float(r["size"]) if r["size"] else 1.0,
                        "timestamp": r["timestamp"],
                    }
                    for r in reversed(rows)
                ]
                return trades
    except Exception:
        pass

    try:
        cache_path = Path(__file__).parent.parent / "data" / "market_cache"
        if cache_path.exists():
            for f in cache_path.glob(f"{market_id}*.json"):
                import json
                data = json.loads(f.read_text())
                trades = data.get("trades", [])
                if trades:
                    return trades
    except Exception:
        pass

    return None


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json as json_lib

    parser = argparse.ArgumentParser(description="VPIN informed trading detection")
    parser.add_argument("--test", action="store_true", help="Run synthetic test suite")
    parser.add_argument("--market", type=str, help="Market ID to compute VPIN for")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    if args.test or args.market:
        print("=== VPIN Synthetic Tests ===\n")

        # Test 1: Balanced buy/sell → VPIN < 0.4 → allow
        buy_v = [25, 30, 20, 28, 22]
        sell_v = [25, 30, 20, 28, 22]
        vpin = compute_vpin(buy_v, sell_v)
        decision = get_vpin_gate_decision(vpin[-1] if len(vpin) else 0.0)
        print(f"Test 1 - Balanced (25/25/20/28/22): VPIN={vpin[-1]:.3f} action={decision['action']}")
        assert decision["action"] == "allow", f"Expected allow, got {decision['action']}"
        print("  ✓ PASS: Balanced flow → action=allow\n")

        # Test 2: One-sided flow (90% buys) → VPIN > 0.7 → halt
        buy_v = [90, 95, 88, 92, 85]
        sell_v = [10, 5, 12, 8, 15]
        vpin = compute_vpin(buy_v, sell_v)
        decision = get_vpin_gate_decision(vpin[-1] if len(vpin) else 0.0)
        print(f"Test 2 - One-sided (90/10): VPIN={vpin[-1]:.3f} action={decision['action']}")
        assert decision["action"] == "halt", f"Expected halt, got {decision['action']}"
        print("  ✓ PASS: One-sided flow → action=halt\n")

        # Test 3: Zero volume → graceful fallback
        vpin = compute_vpin([], [])
        decision = get_vpin_gate_decision(vpin[-1] if len(vpin) else 0.0)
        print(f"Test 3 - Zero volume: VPIN={vpin[-1] if len(vpin) else 0.0:.3f} action={decision['action']}")
        assert decision["action"] == "allow", f"Expected allow, got {decision['action']}"
        print("  ✓ PASS: Zero volume → action=allow\n")

        # Test 4: Above warn threshold (0.65) → widen
        buy_v = [83]
        sell_v = [17]
        vpin = compute_vpin(buy_v, sell_v)
        decision = get_vpin_gate_decision(vpin[-1] if len(vpin) else 0.0)
        print(f"Test 4 - Above warn threshold (83/17): VPIN={vpin[-1]:.3f} action={decision['action']}")
        assert decision["action"] == "widen", f"Expected widen, got {decision['action']}"
        print("  ✓ PASS: Above warn threshold → action=widen\n")

        # Test 5: Single bucket edge case
        buy_v = [50]
        sell_v = [50]
        vpin = compute_vpin(buy_v, sell_v)
        decision = get_vpin_gate_decision(vpin[-1] if len(vpin) else 0.0)
        print(f"Test 5 - Single bucket (50/50): VPIN={vpin[-1]:.3f} action={decision['action']}")
        assert decision["action"] == "allow", f"Expected allow, got {decision['action']}"
        print("  ✓ PASS: Single bucket → valid result\n")

        # Test 6: Market VPIN with no data
        result = get_market_vpin("nonexistent-market-xyz")
        print(f"Test 6 - No data fallback: action={result['action']} reason={result['reason']}")
        assert result["action"] == "allow", f"Expected allow, got {result['action']}"
        print("  ✓ PASS: No data → action=allow\n")

        print("=== All VPIN Tests Passed ===")

    else:
        parser.print_help()