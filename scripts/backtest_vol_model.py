#!/usr/bin/env python3
"""
Retrospective Kalshi vol-model backtest.

This script reconstructs settled trades from pnl.db, optionally enriches them
with read-only paginated Kalshi portfolio history, recomputes a historical
volatility probability as of each trade timestamp, and compares scenario
filters against realized PnL.

No orders are placed or canceled. The only network calls are GET requests to
Kalshi portfolio endpoints and yfinance historical downloads.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import pickle
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from providers.vol_model import apply_probability_shrinkage, blend_probability, parse_kalshi_index_ticker

TRADING_DAYS_PER_YEAR = 252
DEFAULT_AI_PROXY = 0.65
YFINANCE_CACHE_DIR = Path("/tmp/pm_updown_yfinance_cache")
PNL_DB = ROOT / "paper_trading" / "pnl.db"

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

SYMBOL_MAP = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}


@dataclass(frozen=True)
class Scenario:
    name: str
    min_price: float
    max_price: Optional[float]
    max_dte: float
    use_vol_model: bool
    require_vol_prob: Optional[float] = None
    vol_only: bool = False


SCENARIOS = [
    Scenario("A Old config", min_price=0.05, max_price=None, max_dte=14, use_vol_model=False),
    Scenario("B Current config", min_price=0.25, max_price=None, max_dte=3, use_vol_model=True),
    Scenario("C Tighter", min_price=0.30, max_price=None, max_dte=3, use_vol_model=True),
    Scenario("D Mid-range focus", min_price=0.35, max_price=0.55, max_dte=3, use_vol_model=True),
    Scenario("E Wider DTE", min_price=0.25, max_price=None, max_dte=7, use_vol_model=True),
    Scenario("F Vol model only", min_price=0.25, max_price=None, max_dte=3, use_vol_model=True, require_vol_prob=0.30, vol_only=True),
]


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for parser in (
        lambda s: datetime.fromisoformat(s),
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S"),
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f"),
    ):
        try:
            dt = parser(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def normalize_price(value: Any) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    if price > 1.0 and price <= 100.0:
        return price / 100.0
    return price


def parse_contract_ticker(ticker: str) -> Optional[dict[str, Any]]:
    """Parse index/crypto Kalshi threshold and range tickers.

    The production parser is used first for compatibility, then extended here
    for below/range contracts and KXNASDAQ100 tickers seen in pnl.db.
    """
    if not ticker:
        return None

    existing = parse_kalshi_index_ticker(ticker)
    upper = ticker.upper()

    import re

    threshold = re.match(
        r"^(KXINXU?|KXNDXU?|KXNASDAQ100U?|KXBTCU?|KXETHU?)-(\d{2})([A-Z]{3})(\d{2})H\d{4}-T([0-9]+(?:\.[0-9]+)?)$",
        upper,
    )
    if threshold:
        raw_prefix, yy, mon, dd, strike = threshold.groups()
        expiry = _expiry_date(yy, mon, dd)
        if expiry is None:
            return existing
        asset = _asset_from_prefix(raw_prefix)
        if asset is None:
            return existing
        direction = "above" if raw_prefix.endswith("U") or asset in {"BTC", "ETH"} else "below"
        return {
            "asset": asset,
            "symbol": SYMBOL_MAP[asset],
            "strike": float(strike),
            "expiry_date": expiry,
            "direction": direction,
            "contract_type": "threshold",
            "lower": None,
            "upper": None,
        }

    range_match = re.match(
        r"^(KXINX|KXNDX|KXNASDAQ100)-(\d{2})([A-Z]{3})(\d{2})H\d{4}-B([0-9]+(?:\.[0-9]+)?)$",
        upper,
    )
    if range_match:
        raw_prefix, yy, mon, dd, center = range_match.groups()
        expiry = _expiry_date(yy, mon, dd)
        asset = _asset_from_prefix(raw_prefix)
        if expiry is None or asset is None:
            return None
        center_float = float(center)
        width = 25.0 if asset == "SPX" else 100.0
        lower = center_float - (width / 2.0)
        upper_bound = center_float + (width / 2.0)
        return {
            "asset": asset,
            "symbol": SYMBOL_MAP[asset],
            "strike": center_float,
            "expiry_date": expiry,
            "direction": "range",
            "contract_type": "range",
            "lower": lower,
            "upper": upper_bound,
        }

    if existing:
        prefix = existing.get("prefix")
        asset = "SPX" if prefix == "KXINX" else "NDX" if prefix == "KXNDX" else None
        if asset:
            return {
                "asset": asset,
                "symbol": SYMBOL_MAP[asset],
                "strike": float(existing["strike"]),
                "expiry_date": existing["expiry_date"],
                "direction": existing.get("direction", "above"),
                "contract_type": "threshold",
                "lower": None,
                "upper": None,
            }
    return None


def _expiry_date(yy: str, mon: str, dd: str) -> Optional[date]:
    month = MONTHS.get(mon)
    if month is None:
        return None
    try:
        return date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None


def _asset_from_prefix(prefix: str) -> Optional[str]:
    if prefix.startswith("KXINX"):
        return "SPX"
    if prefix.startswith("KXNDX") or prefix.startswith("KXNASDAQ100"):
        return "NDX"
    if prefix.startswith("KXBTC"):
        return "BTC"
    if prefix.startswith("KXETH"):
        return "ETH"
    return None


def load_settled_trades(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(f"pnl.db not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM trades
            WHERE (
                status IN ('settled_won', 'settled_lost', 'settled', 'closed')
                OR realized_pnl_usd IS NOT NULL
                OR settled_price IS NOT NULL
            )
            ORDER BY timestamp ASC, id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    trades = []
    for row in rows:
        rec = dict(row)
        price = normalize_price(rec.get("price"))
        if price is None:
            continue
        actual_pnl = rec.get("realized_pnl_usd")
        if actual_pnl is None:
            actual_pnl = rec.get("pnl_usd") or 0.0
        try:
            actual_pnl = float(actual_pnl)
        except (TypeError, ValueError):
            actual_pnl = 0.0
        rec["price_norm"] = price
        rec["actual_pnl_usd"] = actual_pnl
        rec["trade_dt"] = parse_dt(rec.get("timestamp"))
        rec["expiration_dt"] = parse_dt(rec.get("expiration"))
        rec["side"] = "yes"
        trades.append(rec)
    return trades


def fetch_kalshi_paginated(kind: str, limit: int = 200, max_pages: int = 100) -> tuple[list[dict[str, Any]], str]:
    """Fetch read-only Kalshi portfolio history if env credentials are present."""
    if not (os.getenv("KALSHI_TRADING_KEY") or os.getenv("KALSHI_KEY")):
        return [], "skipped_no_api_key_env"

    try:
        from utils.kalshi_orders import KalshiOrderClient

        client = KalshiOrderClient()
    except Exception as exc:
        return [], f"skipped_client_init_failed:{type(exc).__name__}"

    if kind == "orders":
        path = "/portfolio/orders"
        root_key = "orders"
        params: dict[str, Any] = {"limit": limit}
    elif kind == "fills":
        path = "/portfolio/fills"
        root_key = "fills"
        params = {"limit": limit}
    else:
        raise ValueError(kind)

    items: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    pages = 0
    try:
        while pages < max_pages:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            data = client._request("GET", path, params=page_params, retries=0)
            batch = data.get(root_key, [])
            if not isinstance(batch, list):
                return items, f"partial_unexpected_{kind}_payload"
            items.extend(batch)
            pages += 1
            cursor = data.get("cursor") or data.get("next_cursor")
            if not cursor or not batch:
                break
            time.sleep(0.15)
    except Exception as exc:
        if items:
            return items, f"partial_{kind}_fetch_failed:{type(exc).__name__}"
        return [], f"skipped_{kind}_fetch_failed:{type(exc).__name__}"

    return items, f"ok_pages={pages}_items={len(items)}"


def enrich_from_kalshi(
    trades: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> None:
    orders_by_id = {
        str(o.get("order_id")): o
        for o in orders
        if o.get("order_id")
    }
    fills_by_order: dict[str, dict[str, Any]] = {}
    for fill in fills:
        oid = fill.get("order_id")
        if oid and oid not in fills_by_order:
            fills_by_order[str(oid)] = fill

    for trade in trades:
        order = None
        oid = trade.get("kalshi_order_id")
        if oid:
            order = orders_by_id.get(str(oid))
        if order:
            trade["kalshi_order_status"] = order.get("status")
            trade["side"] = (order.get("side") or trade.get("side") or "yes").lower()
            api_price = (
                order.get("yes_price_dollars")
                if trade["side"] == "yes"
                else order.get("no_price_dollars")
            )
            norm_api_price = normalize_price(api_price)
            if norm_api_price is not None:
                trade["price_norm"] = norm_api_price
        fill = fills_by_order.get(str(oid)) if oid else None
        if fill:
            trade["kalshi_fill_id"] = fill.get("trade_id") or fill.get("fill_id")
            trade["side"] = (fill.get("side") or trade.get("side") or "yes").lower()


def yfinance_history(symbol: str, decision_dt: datetime) -> Optional[list[float]]:
    import yfinance as yf

    YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    end_date = decision_dt.date()
    start_date = end_date - timedelta(days=70)
    cache_key = hashlib.sha256(f"{symbol}|{start_date}|{end_date}".encode("utf-8")).hexdigest()[:20]
    cache_path = YFINANCE_CACHE_DIR / f"{cache_key}.pkl"

    if cache_path.exists():
        try:
            with open(cache_path, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            cache_path.unlink(missing_ok=True)

    try:
        data = yf.download(
            symbol,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            progress=False,
            auto_adjust=True,
            threads=False,
        )
    except Exception:
        return None

    if data is None or data.empty or "Close" not in data:
        return None

    close_data = data["Close"]
    if hasattr(close_data, "columns"):
        if len(close_data.columns) == 0:
            return None
        close_data = close_data.iloc[:, 0]
    closes = [float(x) for x in close_data.dropna().tolist()]
    if len(closes) < 12:
        return None
    closes = closes[-30:]
    try:
        with open(cache_path, "wb") as fh:
            pickle.dump(closes, fh)
    except Exception:
        pass
    return closes


def realized_vol_10d(closes: list[float]) -> Optional[float]:
    returns = []
    for prev, current in zip(closes, closes[1:]):
        if prev > 0 and current > 0:
            returns.append(math.log(current / prev))
    returns = returns[-10:]
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def normal_cdf(x: float) -> float:
    try:
        from scipy.stats import norm

        return float(norm.cdf(x))
    except Exception:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def lognormal_prob(
    current_price: float,
    annual_vol: float,
    days_to_expiry: float,
    direction: str,
    strike: Optional[float] = None,
    lower: Optional[float] = None,
    upper: Optional[float] = None,
) -> Optional[dict[str, float]]:
    if current_price <= 0 or annual_vol < 0 or days_to_expiry <= 0:
        return None
    vol_expiry = annual_vol * math.sqrt(days_to_expiry / TRADING_DAYS_PER_YEAR)
    if vol_expiry <= 0:
        return None

    def cdf_at(level: float) -> float:
        z = math.log(level / current_price) / vol_expiry
        return normal_cdf(z)

    if direction == "above" and strike:
        z_score = math.log(strike / current_price) / vol_expiry
        prob = 1.0 - normal_cdf(z_score)
    elif direction == "below" and strike:
        z_score = math.log(strike / current_price) / vol_expiry
        prob = normal_cdf(z_score)
    elif direction == "range" and lower and upper:
        prob = max(0.0, cdf_at(upper) - cdf_at(lower))
        center = (lower + upper) / 2.0
        z_score = math.log(center / current_price) / vol_expiry
    else:
        return None

    expected_move = current_price * vol_expiry
    return {
        "vol_prob": max(0.0, min(1.0, prob)),
        "z_score": z_score,
        "expected_move": expected_move,
    }


def reconstruct_trade(trade: dict[str, Any]) -> dict[str, Any]:
    ticker = trade.get("ticker") or trade.get("kalshi_market_id") or ""
    decision_dt = trade.get("trade_dt")
    expiration_dt = trade.get("expiration_dt")
    price = trade["price_norm"]
    ai_prob = trade.get("ai_probability")
    try:
        ai_prob = float(ai_prob) if ai_prob is not None else DEFAULT_AI_PROXY
    except (TypeError, ValueError):
        ai_prob = DEFAULT_AI_PROXY
    ai_prob = max(0.0, min(1.0, ai_prob))

    if not decision_dt:
        decision_dt = datetime.now(timezone.utc)

    dte = trade.get("days_to_expiry")
    try:
        dte = float(dte) if dte is not None else None
    except (TypeError, ValueError):
        dte = None
    if (dte is None or dte <= 0) and expiration_dt:
        dte = max((expiration_dt - decision_dt).total_seconds() / 86400.0, 0.0)
    if dte is None:
        dte = 999.0

    parsed = parse_contract_ticker(ticker)
    side = (trade.get("side") or "yes").lower()

    result = {
        **trade,
        "ticker": ticker,
        "side": side,
        "ai_prob_used": ai_prob,
        "days_to_expiry_used": dte,
        "parsed_asset": parsed.get("asset") if parsed else None,
        "parsed_direction": parsed.get("direction") if parsed else None,
        "vol_prob": None,
        "blended_prob": None,
        "adjusted_prob": None,
        "model_status": "unparsed",
        "model_edge_pct": None,
    }

    vol_prob = None
    if parsed and dte > 0:
        closes = yfinance_history(parsed["symbol"], decision_dt)
        if closes:
            rv = realized_vol_10d(closes)
            if rv is not None:
                prob = lognormal_prob(
                    current_price=closes[-1],
                    annual_vol=rv,
                    days_to_expiry=dte,
                    direction=parsed["direction"],
                    strike=parsed.get("strike"),
                    lower=parsed.get("lower"),
                    upper=parsed.get("upper"),
                )
                if prob:
                    vol_prob = prob["vol_prob"]
                    result.update(
                        {
                            "vol_prob": vol_prob,
                            "current_price_asof": closes[-1],
                            "realized_vol_10d": rv,
                            "z_score": prob["z_score"],
                            "expected_move": prob["expected_move"],
                            "model_status": "vol_model",
                        }
                    )
                else:
                    result["model_status"] = "vol_probability_failed"
            else:
                result["model_status"] = "vol_failed"
        else:
            result["model_status"] = "history_missing"

    if vol_prob is not None:
        event_prob = blend_probability(ai_prob, vol_prob)
        result["blended_prob"] = event_prob
    else:
        event_prob = apply_probability_shrinkage(ai_prob)
    if side == "no":
        win_prob = 1.0 - event_prob
    else:
        win_prob = event_prob

    result["adjusted_prob"] = max(0.0, min(1.0, win_prob))
    result["model_edge_pct"] = edge_pct(win_prob, price)
    return result


def edge_pct(probability: float, market_price: float) -> float:
    if market_price <= 0:
        return 0.0
    return ((probability - market_price) / market_price) * 100.0


def scenario_probability(row: dict[str, Any], scenario: Scenario) -> float:
    ai_prob = float(row.get("ai_prob_used") or DEFAULT_AI_PROXY)
    side = (row.get("side") or "yes").lower()
    if scenario.vol_only:
        event_prob = row.get("vol_prob")
        if event_prob is None:
            return -1.0
    elif scenario.use_vol_model:
        event_prob = row.get("blended_prob")
        if event_prob is None:
            event_prob = apply_probability_shrinkage(ai_prob)
    else:
        event_prob = ai_prob
    if side == "no":
        return 1.0 - float(event_prob)
    return float(event_prob)


def scenario_pass(row: dict[str, Any], scenario: Scenario) -> tuple[bool, float]:
    price = float(row["price_norm"])
    dte = float(row.get("days_to_expiry_used") or 999.0)
    if price < scenario.min_price:
        return False, 0.0
    if scenario.max_price is not None and price > scenario.max_price:
        return False, 0.0
    if dte > scenario.max_dte:
        return False, 0.0
    if scenario.require_vol_prob is not None:
        vol_prob = row.get("vol_prob")
        if vol_prob is None or float(vol_prob) <= scenario.require_vol_prob:
            return False, 0.0
    prob = scenario_probability(row, scenario)
    if prob < 0:
        return False, 0.0
    edge = edge_pct(prob, price)
    return edge > 0.0, edge


def summarize_scenario(rows: list[dict[str, Any]], scenario: Scenario) -> dict[str, Any]:
    selected = []
    edges = []
    for row in rows:
        passed, edge = scenario_pass(row, scenario)
        if passed:
            selected.append(row)
            edges.append(edge)

    pnl_values = [float(r.get("actual_pnl_usd") or 0.0) for r in selected]
    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p < 0]
    count = len(selected)
    sharpe_like = None
    if len(pnl_values) >= 2:
        std = statistics.stdev(pnl_values)
        if std > 0:
            sharpe_like = statistics.mean(pnl_values) / std

    return {
        "scenario": scenario.name,
        "trades": count,
        "win_rate": (len(wins) / count) if count else 0.0,
        "net_pnl_usd": sum(pnl_values),
        "avg_pnl_usd": statistics.mean(pnl_values) if pnl_values else 0.0,
        "avg_edge_pct": statistics.mean(edges) if edges else 0.0,
        "sharpe_like": sharpe_like,
        "gross_wins": sum(wins),
        "gross_losses": sum(losses),
    }


def write_results(rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]], output_dir: Path, notes: list[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "backtest_results.csv"
    summary_path = output_dir / "backtest_summary.txt"

    base_fields = [
        "id",
        "ticker",
        "timestamp",
        "expiration",
        "side",
        "price_norm",
        "size_usd",
        "actual_pnl_usd",
        "settled_price",
        "status",
        "market_category",
        "ai_prob_used",
        "days_to_expiry_used",
        "parsed_asset",
        "parsed_direction",
        "model_status",
        "vol_prob",
        "blended_prob",
        "adjusted_prob",
        "model_edge_pct",
        "current_price_asof",
        "realized_vol_10d",
        "z_score",
        "expected_move",
    ]
    scenario_fields = []
    for scenario in SCENARIOS:
        key = scenario.name.split(" ", 1)[0]
        scenario_fields.extend([f"{key}_pass", f"{key}_edge_pct"])
    fields = base_fields + scenario_fields

    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            for scenario in SCENARIOS:
                key = scenario.name.split(" ", 1)[0]
                passed, edge = scenario_pass(row, scenario)
                out[f"{key}_pass"] = passed
                out[f"{key}_edge_pct"] = edge
            writer.writerow(out)

    lines = []
    lines.append("Vol-model backtest summary")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Settled trades processed: {len(rows)}")
    lines.append(f"Actual settled PnL in processed DB rows: ${sum(float(r.get('actual_pnl_usd') or 0.0) for r in rows):.2f}")
    lines.append(f"Vol-model rows: {sum(1 for r in rows if r.get('model_status') == 'vol_model')}")
    lines.append(f"Unparsed rows: {sum(1 for r in rows if r.get('model_status') == 'unparsed')}")
    lines.append("")
    if notes:
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    lines.append(format_summary_table(summary_rows))
    best = max(summary_rows, key=lambda r: (r["net_pnl_usd"], r["trades"])) if summary_rows else None
    if best:
        lines.append("")
        lines.append(f"Best scenario by net PnL: {best['scenario']} (${best['net_pnl_usd']:.2f}, trades={best['trades']})")
    lines.append("")
    lines.append("Caveat: this is retrospective filtering over trades already taken. It does not prove future profitability.")

    summary_path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nSaved CSV: {csv_path}")
    print(f"Saved summary: {summary_path}")


def format_summary_table(summary_rows: list[dict[str, Any]]) -> str:
    headers = ["Scenario", "Trades", "Win%", "Net PnL", "Avg Edge%", "Sharpe-like"]
    widths = [22, 8, 8, 10, 10, 12]
    lines = [
        " ".join(h.ljust(w) for h, w in zip(headers, widths)),
        " ".join("-" * w for w in widths),
    ]
    for row in summary_rows:
        sharpe = row["sharpe_like"]
        values = [
            row["scenario"],
            str(row["trades"]),
            f"{row['win_rate'] * 100:.1f}",
            f"{row['net_pnl_usd']:.2f}",
            f"{row['avg_edge_pct']:.1f}",
            "n/a" if sharpe is None else f"{sharpe:.2f}",
        ]
        lines.append(" ".join(v.ljust(w) for v, w in zip(values, widths)))
    return "\n".join(lines)


def write_api_snapshot(output_dir: Path, orders: list[dict[str, Any]], fills: list[dict[str, Any]], statuses: list[str]) -> None:
    """Write non-secret API metadata for proof/debugging."""
    safe = {
        "statuses": statuses,
        "orders_count": len(orders),
        "fills_count": len(fills),
        "order_status_counts": {},
    }
    for order in orders:
        status = str(order.get("status") or "unknown")
        safe["order_status_counts"][status] = safe["order_status_counts"].get(status, 0) + 1
    (output_dir / "kalshi_api_snapshot.json").write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PNL_DB)
    parser.add_argument("--output-dir", type=Path, default=Path(os.getenv("PROOF_DIR", "/tmp/backtest_vol_model")))
    parser.add_argument("--skip-kalshi-api", action="store_true", help="Do not attempt read-only Kalshi portfolio pagination")
    parser.add_argument("--load-dotenv", action="store_true", help="Load .env before Kalshi API enrichment")
    parser.add_argument("--max-api-pages", type=int, default=100)
    args = parser.parse_args()

    if args.load_dotenv:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

    notes: list[str] = []
    trades = load_settled_trades(args.db)

    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    api_statuses = []
    if args.skip_kalshi_api:
        api_statuses.append("kalshi_api_skipped_by_flag")
        notes.append("Kalshi API pagination skipped by flag; reconstruction used pnl.db only.")
    else:
        orders, order_status = fetch_kalshi_paginated("orders", max_pages=args.max_api_pages)
        fills, fill_status = fetch_kalshi_paginated("fills", max_pages=args.max_api_pages)
        api_statuses.extend([f"orders:{order_status}", f"fills:{fill_status}"])
        if orders or fills:
            enrich_from_kalshi(trades, orders, fills)
            notes.append(f"Kalshi API enrichment attempted: orders={len(orders)}, fills={len(fills)}.")
        else:
            notes.append(f"Kalshi API enrichment unavailable ({order_status}; {fill_status}); reconstruction used pnl.db only.")

    rows = [reconstruct_trade(t) for t in trades]
    summary_rows = [summarize_scenario(rows, s) for s in SCENARIOS]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_api_snapshot(args.output_dir, orders, fills, api_statuses)
    write_results(rows, summary_rows, args.output_dir, notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
