"""Real-asset price feed provider for Kalshi market AI prior enrichment.

Maps Kalshi market ticker prefixes to yfinance symbols, fetches current
price data, and provides structured context for injection into LLM prompts.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

TICKER_MAP = {
    "KXINX": "^GSPC",
    "KXINXU": "^GSPC",
    "INX": "^GSPC",
    "KXNDX": "^IXIC",
    "NDX": "^IXIC",
    "KXBTC": "BTC-USD",
    "BTC": "BTC-USD",
    "KXETH": "ETH-USD",
    "KXETHY": "ETH-USD",
    "ETH": "ETH-USD",
}

CACHE_TTL_SECONDS = 300

_cache: dict[str, tuple[dict, float]] = {}


def _extract_prefix(kalshi_ticker: str) -> Optional[str]:
    if not kalshi_ticker:
        return None
    m = re.match(r"^([A-Z]+)", kalshi_ticker.upper())
    return m.group(1) if m else None


def _resolve_yfinance_symbol(kalshi_ticker: str) -> Optional[str]:
    prefix = _extract_prefix(kalshi_ticker)
    if not prefix:
        return None
    return TICKER_MAP.get(prefix)


def _compute_volatility_5d(prices: list[float]) -> float:
    if len(prices) < 2:
        return 0.0
    returns = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev == 0:
            continue
        returns.append((prices[i] - prev) / prev)
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance) * 100.0


def _fetch_yfinance(symbol: str) -> Optional[dict]:
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info

        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        high_52w = info.get("fiftyTwoWeekHigh")
        low_52w = info.get("fiftyTwoWeekLow")

        if current_price is None or prev_close is None:
            logger.warning("[price_feed] Incomplete data for %s: price=%s prev=%s", symbol, current_price, prev_close)
            return None

        change_pct = ((current_price - prev_close) / prev_close) * 100.0 if prev_close else 0.0

        hist = ticker.history(period="5d")
        last_5d_prices = []
        if hist is not None and not hist.empty:
            last_5d_prices = hist["Close"].tolist()
            if len(last_5d_prices) > 5:
                last_5d_prices = last_5d_prices[-5:]

        volatility_5d = _compute_volatility_5d(last_5d_prices)

        return {
            "current_price": float(current_price),
            "prev_close": float(prev_close),
            "change_pct": round(change_pct, 2),
            "high_52w": float(high_52w) if high_52w is not None else None,
            "low_52w": float(low_52w) if low_52w is not None else None,
            "last_5d_prices": [round(p, 2) for p in last_5d_prices],
            "volatility_5d": round(volatility_5d, 2),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
        }
    except Exception as exc:
        logger.warning("[price_feed] yfinance fetch failed for %s: %s", symbol, exc)
        return None


class PriceFeedProvider:
    """Provides real-time price context for Kalshi market tickers."""

    def __init__(self, cache_ttl: int = CACHE_TTL_SECONDS):
        self._cache_ttl = cache_ttl

    def get_price_context(self, kalshi_ticker: str) -> Optional[dict]:
        symbol = _resolve_yfinance_symbol(kalshi_ticker)
        if not symbol:
            return None

        now = time.time()
        cached = _cache.get(symbol)
        if cached and (now - cached[1]) < self._cache_ttl:
            logger.debug("[price_feed] Cache hit for %s (%s)", kalshi_ticker, symbol)
            return cached[0]

        logger.info("[price_feed] Fetching %s -> %s", kalshi_ticker, symbol)
        data = _fetch_yfinance(symbol)
        if data is None:
            return None

        _cache[symbol] = (data, now)
        return data

    @staticmethod
    def format_price_context(ctx: dict) -> str:
        if not ctx:
            return ""
        current = ctx["current_price"]
        change = ctx["change_pct"]
        prev_close = ctx["prev_close"]
        prices_5d = ctx.get("last_5d_prices", [])
        vol_5d = ctx.get("volatility_5d", 0.0)

        lines = [
            f"## Current Market Data",
            f"Underlying asset is currently trading at ${current:,.2f} ({change:+.2f}% today).",
            f"Previous close: ${prev_close:,.2f}.",
        ]

        if prices_5d:
            low = min(prices_5d)
            high = max(prices_5d)
            lines.append(f"5-day range: ${low:,.2f}\u2013${high:,.2f}.")

        if vol_5d > 0:
            lines.append(f"5-day volatility: {vol_5d:.2f}%.")

        return "\n".join(lines)


_feed_provider: Optional[PriceFeedProvider] = None


def get_feed_provider() -> PriceFeedProvider:
    global _feed_provider
    if _feed_provider is None:
        _feed_provider = PriceFeedProvider()
    return _feed_provider


def enrich_market_text(market_text: str, market_ticker: Optional[str] = None) -> str:
    if not market_ticker:
        return market_text

    provider = get_feed_provider()
    ctx = provider.get_price_context(market_ticker)
    if ctx is None:
        return market_text

    price_section = PriceFeedProvider.format_price_context(ctx)
    if not price_section:
        return market_text

    return f"{market_text}\n\n{price_section}"
