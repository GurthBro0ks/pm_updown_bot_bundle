#!/usr/bin/env python3
"""
Unified Price Fetcher
Centralized service for fetching stock and crypto prices.
Used by stock_hunter and rotation_manager.
"""

import os
import time
import logging
import requests
from typing import Optional, Dict, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# API Keys (imported from config)
from config import FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, MASSIVE_API_KEY

# Cache for API responses
_price_cache = {}
CACHE_DURATION_S = 900  # 15 minutes

# Rate limiting
MASSIVE_DELAY_S = 15
last_massive_call = 0

# Global requests session
_request_session = requests.Session()
API_TIMEOUT = 15
API_RETRIES = 2


def safe_request(url: str, timeout: int = API_TIMEOUT, retries: int = API_RETRIES) -> Optional[requests.Response]:
    """Safe HTTP request with timeout and retry handling"""
    for attempt in range(retries):
        try:
            resp = _request_session.get(url, timeout=timeout)
            return resp
        except requests.Timeout:
            logger.warning(f"Request timeout (attempt {attempt+1}/{retries}): {url[:50]}...")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                return None
        except requests.ConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt+1}/{retries}): {str(e)[:50]}")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                return None
    return None


def fetch_stock_price(ticker: str, use_cache: bool = True) -> Optional[float]:
    """
    Fetch current stock price using available APIs.

    Tries in order:
    1. Massive (Polygon) - primary
    2. Alpha Vantage - fallback
    3. Finnhub - fallback

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL')
        use_cache: Whether to use cached prices

    Returns:
        Current price in USD, or None if unavailable
    """
    # Check cache first
    cache_key = f"price_{ticker}"
    if use_cache and cache_key in _price_cache:
        cached = _price_cache[cache_key]
        if time.time() - cached["timestamp"] < CACHE_DURATION_S:
            logger.debug(f"Using cached price for {ticker}: ${cached['price']:.2f}")
            return cached["price"]

    price = None

    # Try Massive first
    if MASSIVE_API_KEY:
        price = _fetch_massive_price(ticker)
        if price:
            _price_cache[cache_key] = {"price": price, "timestamp": time.time()}
            return price

    # Try Alpha Vantage
    if ALPHA_VANTAGE_API_KEY:
        price = _fetch_alpha_vantage_price(ticker)
        if price:
            _price_cache[cache_key] = {"price": price, "timestamp": time.time()}
            return price

    # Try Finnhub
    if FINNHUB_API_KEY:
        price = _fetch_finnhub_price(ticker)
        if price:
            _price_cache[cache_key] = {"price": price, "timestamp": time.time()}
            return price

    logger.warning(f"Could not fetch price for {ticker}")
    return None


def _fetch_massive_price(ticker: str) -> Optional[float]:
    """Fetch stock price from Massive (Polygon) API"""
    global last_massive_call

    if not MASSIVE_API_KEY:
        return None

    # Rate limiting
    elapsed = time.time() - last_massive_call
    if elapsed < MASSIVE_DELAY_S:
        wait_time = MASSIVE_DELAY_S - elapsed
        logger.debug(f"Rate limiting: waiting {wait_time:.1f}s before Massive call")
        time.sleep(wait_time)

    try:
        url = f"https://api.massive.com/v2/aggs/ticker/{ticker}/prev?apiKey={MASSIVE_API_KEY}"
        resp = safe_request(url)
        if resp is None:
            return None

        last_massive_call = time.time()

        if resp.status_code == 429:
            logger.warning(f"Massive API rate limited for {ticker}")
            return None

        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "OK" and data.get("results"):
            result = data["results"][0]
            price = result.get("c")  # Close price
            logger.info(f"Massive: {ticker} = ${price:.2f}")
            return price
        return None
    except Exception as e:
        logger.error(f"Massive error for {ticker}: {e}")
        return None


def _fetch_alpha_vantage_price(ticker: str) -> Optional[float]:
    """Fetch stock price from Alpha Vantage"""
    if not ALPHA_VANTAGE_API_KEY:
        return None

    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
        resp = safe_request(url)
        if resp is None:
            return None

        resp.raise_for_status()
        data = resp.json()

        quote = data.get("Global Quote", {})
        price_str = quote.get("05. price")
        if price_str:
            price = float(price_str)
            logger.info(f"Alpha Vantage: {ticker} = ${price:.2f}")
            return price
        return None
    except Exception as e:
        logger.error(f"Alpha Vantage error for {ticker}: {e}")
        return None


def _fetch_finnhub_price(ticker: str) -> Optional[float]:
    """Fetch stock price from Finnhub"""
    if not FINNHUB_API_KEY:
        return None

    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}"
        resp = safe_request(url)
        if resp is None:
            return None

        resp.raise_for_status()
        data = resp.json()

        # Finnhub response: {"c": current, "h": high, "l": low, "o": open, "pc": prev_close, "t": timestamp}
        price = data.get("c")
        if price and price > 0:
            logger.info(f"Finnhub: {ticker} = ${price:.2f}")
            return price
        return None
    except Exception as e:
        logger.error(f"Finnhub error for {ticker}: {e}")
        return None


def fetch_crypto_price(symbol: str, exchange: str = "binance") -> Optional[float]:
    """
    Fetch cryptocurrency price using ccxt library.

    Args:
        symbol: Crypto symbol (e.g., 'BTC/USDT')
        exchange: Exchange name (default: binance)

    Returns:
        Current price in USD, or None if unavailable
    """
    try:
        import ccxt
    except ImportError:
        logger.warning("ccxt not installed, cannot fetch crypto prices")
        return None

    try:
        exchange_class = getattr(ccxt, exchange)
        exchange_obj = exchange_class()
        ticker = exchange_obj.fetch_ticker(symbol)
        price = ticker.get("last")
        if price:
            logger.info(f"Crypto: {symbol} = ${price:.2f}")
            return price
        return None
    except Exception as e:
        logger.error(f"Crypto fetch error for {symbol}: {e}")
        return None


def fetch_multiple_prices(tickers: List[str], use_cache: bool = True) -> Dict[str, float]:
    """
    Fetch prices for multiple tickers.

    Args:
        tickers: List of stock ticker symbols
        use_cache: Whether to use cached prices

    Returns:
        Dict mapping ticker -> price
    """
    prices = {}
    for ticker in tickers:
        price = fetch_stock_price(ticker, use_cache=use_cache)
        if price:
            prices[ticker] = price
    return prices


def get_cached_price(ticker: str) -> Optional[float]:
    """Get cached price if available"""
    cache_key = f"price_{ticker}"
    if cache_key in _price_cache:
        cached = _price_cache[cache_key]
        if time.time() - cached["timestamp"] < CACHE_DURATION_S:
            return cached["price"]
    return None


def clear_cache():
    """Clear all cached prices"""
    global _price_cache
    _price_cache = {}
    logger.info("Price cache cleared")
