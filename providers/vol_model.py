"""Volatility-based probability model for Kalshi index contracts."""

from __future__ import annotations

import logging
import math
import re
import statistics
import time
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300
TRADING_DAYS_PER_YEAR = 252

TICKER_MAP = {
    "KXINX": "^GSPC",
    "KXINXU": "^GSPC",
    "KXNDX": "^NDX",
    "KXNDXU": "^NDX",
    "KXBTC": "BTC-USD",
    "KXETH": "ETH-USD",
}

_MONTHS = {
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

_cache: dict[tuple[str, float, float, str], tuple[dict, float]] = {}


def clear_cache() -> None:
    _cache.clear()


def blend_probability(ai_prob: float, vol_prob: float) -> float:
    return max(0.0, min(1.0, (0.6 * float(vol_prob)) + (0.4 * float(ai_prob))))


def apply_probability_shrinkage(ai_prob: float) -> float:
    return max(0.0, min(1.0, 0.5 + (float(ai_prob) - 0.5) * 0.5))


def _normalize_prefix(ticker_prefix: str) -> Optional[str]:
    if not ticker_prefix:
        return None
    prefix = ticker_prefix.upper()
    if prefix.startswith("KXINX"):
        return "KXINX"
    if prefix.startswith("KXNDX"):
        return "KXNDX"
    if prefix.startswith("KXBTC"):
        return "KXBTC"
    if prefix.startswith("KXETH"):
        return "KXETH"
    return None


def parse_kalshi_index_ticker(ticker: str) -> Optional[dict]:
    """Parse Kalshi S&P/NDX strike tickers.

    Example: KXINXU-26MAY08H1600-T7374.9999
    """
    if not ticker:
        return None

    match = re.match(
        r"^(KXINXU?|KXNDXU?)-(\d{2})([A-Z]{3})(\d{2})H\d{4}-T([0-9]+(?:\.[0-9]+)?)$",
        ticker.upper(),
    )
    if not match:
        return None

    raw_prefix, yy, mon, dd, strike = match.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None

    prefix = _normalize_prefix(raw_prefix)
    if prefix not in {"KXINX", "KXNDX"}:
        return None

    try:
        expiry_date = date(2000 + int(yy), month, int(dd))
        strike_float = float(strike)
    except ValueError:
        return None

    return {
        "prefix": prefix,
        "strike": strike_float,
        "expiry_date": expiry_date,
        "direction": "above",
    }


def _fetch_market_data(symbol: str) -> Optional[dict]:
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")

        hist = ticker.history(period="45d")
        if hist is None or hist.empty:
            return None

        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        if len(closes) > 30:
            closes = closes[-30:]
        if not closes:
            return None

        if current_price is None:
            current_price = closes[-1]

        return {"current_price": float(current_price), "closes": closes}
    except Exception as exc:
        logger.warning("[VOL_MODEL] yfinance fetch failed for %s: %s", symbol, exc)
        return None


def _compute_realized_vol_10d(closes: list[float]) -> Optional[float]:
    if len(closes) < 3:
        return None

    returns = []
    for prev, current in zip(closes, closes[1:]):
        if prev <= 0 or current <= 0:
            continue
        returns.append(math.log(current / prev))

    returns = returns[-10:]
    if len(returns) < 2:
        return None


    daily_vol = statistics.stdev(returns)
    return daily_vol * math.sqrt(TRADING_DAYS_PER_YEAR)


def _log_normal_probability(
    current_price: float,
    strike: float,
    annual_vol: float,
    days_to_expiry: float,
    direction: str,
) -> Optional[dict]:
    if current_price <= 0 or strike <= 0 or days_to_expiry <= 0:
        return None

    try:
        from scipy.stats import norm
    except Exception as exc:
        logger.warning("[VOL_MODEL] scipy unavailable: %s", exc)
        return None

    vol_expiry = annual_vol * math.sqrt(days_to_expiry / TRADING_DAYS_PER_YEAR)
    expected_move = current_price * vol_expiry

    if vol_expiry <= 0:
        if math.isclose(current_price, strike):
            prob_above = 0.5
            z_score = 0.0
        else:
            prob_above = 1.0 if current_price > strike else 0.0
            z_score = math.inf if strike > current_price else -math.inf
    else:
        z_score = math.log(strike / current_price) / vol_expiry
        prob_above = 1.0 - float(norm.cdf(z_score))

    direction_normalized = direction.lower().strip()
    if direction_normalized == "below":
        vol_prob = 1.0 - prob_above
    elif direction_normalized == "above":
        vol_prob = prob_above
    else:
        return None

    return {
        "vol_prob": max(0.0, min(1.0, vol_prob)),
        "z_score": z_score,
        "expected_move": expected_move,
    }


def compute_vol_probability(
    ticker_prefix: str,
    strike: float,
    days_to_expiry: float,
    direction: str = "above",
) -> Optional[dict]:
    prefix = _normalize_prefix(ticker_prefix)
    if prefix is None:
        return None

    symbol = TICKER_MAP.get(prefix)
    if symbol is None:
        return None

    try:
        strike = float(strike)
        days_to_expiry = float(days_to_expiry)
    except (TypeError, ValueError):
        return None

    if strike <= 0 or days_to_expiry <= 0:
        return None

    cache_key = (prefix, round(strike, 4), round(days_to_expiry, 4), direction.lower())
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and (now - cached[1]) < CACHE_TTL_SECONDS:
        return cached[0]

    data = _fetch_market_data(symbol)
    if data is None:
        return None

    current_price = float(data["current_price"])
    realized_vol = _compute_realized_vol_10d(data.get("closes", []))
    if realized_vol is None:
        return None

    prob = _log_normal_probability(current_price, strike, realized_vol, days_to_expiry, direction)
    if prob is None:
        return None

    result = {
        "vol_prob": prob["vol_prob"],
        "current_price": current_price,
        "realized_vol_10d": realized_vol,
        "z_score": prob["z_score"],
        "expected_move": prob["expected_move"],
        "model": "log_normal_vol",
    }
    _cache[cache_key] = (result, now)
    return result
