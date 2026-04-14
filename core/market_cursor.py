#!/usr/bin/env python3
"""
Market Cursor Module — Rotating cursor for cascade fairness (Reliability Phase 1, Module 1.5).

Over N cron runs, every active market eventually gets priced. No market is
permanently starved by cascade budget exhaustion.

Design: dumb rotation, no priority sorting. Simplicity first.
"""

import json
import logging
import hashlib
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MarketCursor:
    index: int = 0
    list_hash: str = ""
    last_updated: Optional[str] = None
    total_rotations: int = 0


def load_cursor(path: Path) -> MarketCursor:
    """Load cursor from disk. Returns default cursor if file missing or corrupt."""
    try:
        if not path.exists():
            return MarketCursor(index=0, list_hash="", last_updated=datetime.now(timezone.utc).isoformat(), total_rotations=0)
        with open(path, "r") as f:
            data = json.load(f)
        return MarketCursor(
            index=data.get("index", 0),
            list_hash=data.get("list_hash", ""),
            last_updated=data.get("last_updated", datetime.now(timezone.utc).isoformat()),
            total_rotations=data.get("total_rotations", 0),
        )
    except Exception as e:
        logger.warning("[cursor] load_cursor failed (%s), returning default", e)
        return MarketCursor(index=0, list_hash="", last_updated=datetime.now(timezone.utc).isoformat(), total_rotations=0)


def save_cursor(cursor: MarketCursor, path: Path) -> None:
    """Atomic write: write to unique tmp, fsync, rename to path."""
    cursor.last_updated = datetime.now(timezone.utc).isoformat()
    data = asdict(cursor)
    tmp_path = str(path) + f".tmp.{os.getpid()}.{id(data)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, str(path))


def compute_list_hash(market_tickers: list) -> str:
    """SHA-256 of JSON-encoded sorted tickers."""
    if not market_tickers:
        return ""
    sorted_tickers = sorted(str(t) for t in market_tickers)
    payload = json.dumps(sorted_tickers)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rotate_markets(all_markets: list, cursor: MarketCursor):
    """
    Rotate market list starting at cursor index.

    Returns (markets_in_rotation_order, updated_cursor).
    If cursor.list_hash != current hash: reset cursor to 0, update hash.
    If cursor.index > len(all_markets): clamp to 0.
    Does NOT advance the cursor — that happens after cascade via advance_cursor().
    """
    if not all_markets:
        return list(all_markets), cursor, False

    tickers = []
    for m in all_markets:
        t = m.get("ticker") or m.get("id") or ""
        if t:
            tickers.append(t)
    current_hash = compute_list_hash(tickers)

    updated = MarketCursor(
        index=cursor.index,
        list_hash=current_hash,
        last_updated=cursor.last_updated,
        total_rotations=cursor.total_rotations,
    )

    hash_changed = False
    if cursor.list_hash and cursor.list_hash != current_hash:
        updated.index = 0
        hash_changed = True

    if updated.index >= len(all_markets):
        updated.index = 0

    idx = updated.index
    rotated = all_markets[idx:] + all_markets[:idx]

    return rotated, updated, hash_changed


def advance_cursor(cursor: MarketCursor, markets_processed: int, total_markets: int) -> MarketCursor:
    """Advance cursor by markets_processed, wrap at total_markets, increment total_rotations on wrap."""
    if total_markets <= 0:
        return cursor

    new_index = cursor.index + markets_processed
    rotations_completed = 0
    while new_index >= total_markets:
        new_index -= total_markets
        rotations_completed += 1

    return MarketCursor(
        index=new_index,
        list_hash=cursor.list_hash,
        last_updated=cursor.last_updated,
        total_rotations=cursor.total_rotations + rotations_completed,
    )
