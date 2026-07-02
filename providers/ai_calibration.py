"""Historical calibration for raw AI probabilities."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "paper_trading/pnl.db"
CACHE_TTL_SECONDS = 24 * 60 * 60
MIN_CALIBRATION_TRADES = 50
MIN_BUCKET_TRADES = 5

_cache: dict[str, tuple[dict[str, Any], float]] = {}


def clear_cache() -> None:
    _cache.clear()


def _now() -> float:
    return time.time()


def flat_shrinkage(raw_ai_prob: float) -> float:
    prob = max(0.0, min(1.0, float(raw_ai_prob)))
    return max(0.0, min(1.0, 0.5 + (prob - 0.5) * 0.5))


def bucket_name(raw_ai_prob: float) -> str:
    prob = max(0.0, min(1.0, float(raw_ai_prob)))
    idx = min(int(prob * 10), 9)
    return f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}"


def _bucket_midpoint(name: str) -> float:
    low, high = name.split("-", 1)
    return (float(low) + float(high)) / 2.0


def _connect(db_path: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    if isinstance(db_path, sqlite3.Connection):
        db_path.row_factory = sqlite3.Row
        return db_path, False
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn, True


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}


def _is_settled(row: sqlite3.Row, columns: set[str]) -> bool:
    status = str(row["status"] or "").lower() if "status" in columns else ""
    if status in {"settled_won", "settled_lost", "settled", "closed"}:
        return True
    if "realized_pnl_usd" in columns and row["realized_pnl_usd"] is not None:
        return True
    if "settled_price" in columns and row["settled_price"] is not None:
        return True
    return False


def _is_win(row: sqlite3.Row, columns: set[str]) -> bool:
    status = str(row["status"] or "").lower() if "status" in columns else ""
    if status == "settled_won":
        return True
    if status == "settled_lost":
        return False

    for col in ("realized_pnl_usd", "pnl_usd"):
        if col in columns and row[col] is not None:
            try:
                return float(row[col]) > 0.0
            except (TypeError, ValueError):
                return False

    if "settled_price" in columns and row["settled_price"] is not None:
        try:
            return float(row["settled_price"]) >= 0.5
        except (TypeError, ValueError):
            return False
    return False


def _empty_buckets() -> dict[str, dict[str, Any]]:
    return {
        f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}": {
            "count": 0,
            "wins": 0,
            "avg_ai_prob": None,
            "actual_rate": None,
            "bias": None,
        }
        for idx in range(10)
    }


def _build_uncached(db_path: str | Path | sqlite3.Connection) -> dict[str, Any]:
    buckets = _empty_buckets()
    ai_sum_by_bucket = {name: 0.0 for name in buckets}
    total_ai = 0.0
    total_wins = 0
    total = 0

    conn, should_close = _connect(db_path)
    try:
        columns = _existing_columns(conn)
        if "trades" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }:
            raise sqlite3.OperationalError("trades table not found")
        if "ai_probability" not in columns:
            raise sqlite3.OperationalError("trades.ai_probability column not found")

        select_cols = sorted(columns.intersection({
            "ai_probability",
            "status",
            "realized_pnl_usd",
            "pnl_usd",
            "settled_price",
        }))
        rows = conn.execute(
            f"SELECT {', '.join(select_cols)} FROM trades WHERE ai_probability IS NOT NULL"
        ).fetchall()
    finally:
        if should_close:
            conn.close()

    for row in rows:
        if not _is_settled(row, columns):
            continue
        try:
            ai_prob = float(row["ai_probability"])
        except (TypeError, ValueError):
            continue
        if not 0.0 <= ai_prob <= 1.0:
            continue

        name = bucket_name(ai_prob)
        win = _is_win(row, columns)
        buckets[name]["count"] += 1
        buckets[name]["wins"] += 1 if win else 0
        ai_sum_by_bucket[name] += ai_prob
        total += 1
        total_ai += ai_prob
        total_wins += 1 if win else 0

    for name, bucket in buckets.items():
        count = bucket["count"]
        if count:
            actual_rate = bucket["wins"] / count
            avg_ai_prob = ai_sum_by_bucket[name] / count
            bucket["actual_rate"] = actual_rate
            bucket["avg_ai_prob"] = avg_ai_prob
            bucket["bias"] = avg_ai_prob - actual_rate

    overall_actual_rate = (total_wins / total) if total else None
    avg_ai_prob = (total_ai / total) if total else None
    return {
        "buckets": buckets,
        "total_calibration_trades": total,
        "overall_actual_rate": overall_actual_rate,
        "avg_ai_prob": avg_ai_prob,
        "overall_bias": (avg_ai_prob - overall_actual_rate) if total else None,
        "enough_data": total >= MIN_CALIBRATION_TRADES,
        "min_calibration_trades": MIN_CALIBRATION_TRADES,
        "min_bucket_trades": MIN_BUCKET_TRADES,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_calibration_table(db_path: str | Path | sqlite3.Connection = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Build or return a cached calibration table from settled pnl.db trades."""
    if isinstance(db_path, sqlite3.Connection):
        return _build_uncached(db_path)

    key = str(Path(db_path).resolve())
    cached = _cache.get(key)
    now = _now()
    if cached is not None:
        table, built_at = cached
        if now - built_at < CACHE_TTL_SECONDS:
            return table

    table = _build_uncached(db_path)
    _cache[key] = (table, now)
    return table


def nearest_bucket_name(raw_ai_prob: float, calibration_table: dict[str, Any]) -> str | None:
    target = bucket_name(raw_ai_prob)
    buckets = calibration_table.get("buckets", {})
    if buckets.get(target, {}).get("count", 0) >= MIN_BUCKET_TRADES:
        return target

    usable = [
        name for name, bucket in buckets.items()
        if bucket.get("count", 0) >= MIN_BUCKET_TRADES and bucket.get("actual_rate") is not None
    ]
    if not usable:
        return None

    target_mid = _bucket_midpoint(target)
    return min(usable, key=lambda name: abs(_bucket_midpoint(name) - target_mid))


def calibrate_probability(raw_ai_prob: float, calibration_table: dict[str, Any]) -> float:
    """Calibrate a raw AI probability using bucketed historical win rates."""
    raw_ai_prob = max(0.0, min(1.0, float(raw_ai_prob)))
    total = int(calibration_table.get("total_calibration_trades") or 0)
    if total < MIN_CALIBRATION_TRADES:
        return flat_shrinkage(raw_ai_prob)

    selected = nearest_bucket_name(raw_ai_prob, calibration_table)
    if selected is None:
        return flat_shrinkage(raw_ai_prob)

    actual_rate = calibration_table.get("buckets", {}).get(selected, {}).get("actual_rate")
    if actual_rate is None:
        return flat_shrinkage(raw_ai_prob)
    return max(0.0, min(1.0, float(actual_rate)))
