#!/usr/bin/env python3
"""
Sentiment Analysis Module
Centralized service for fetching and calculating sentiment scores.
Extracted from stock_hunter.py.
"""

import os
import json
import logging
import requests
from typing import Optional, Dict, List
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# API Keys (imported from config)
from config import FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, MEME_TICKERS

# Twitter sentiment via Apify
from utils.twitter_sentiment import get_twitter_sentiment

# Global requests session
_request_session = requests.Session()
API_TIMEOUT = 15
API_RETRIES = 2

# Alpha Vantage daily call limit
AV_DAILY_LIMIT = 25
AV_COUNTER_FILE = Path("/opt/slimy/pm_updown_bot_bundle/logs/av_counter.json")

# Earnings calendar multiplier
EARNINGS_MULTIPLIER = 1.20  # 20% boost for stocks with upcoming earnings
EARNINGS_CACHE_DURATION_H = 6  # Cache earnings data for 6 hours

# Earnings cache: {ticker: {"has_earnings": bool, "earnings_date": str, "timestamp": float}}
_earnings_cache = {}


def get_av_call_count() -> int:
    """Get today's Alpha Vantage call count"""
    try:
        if AV_COUNTER_FILE.exists():
            with open(AV_COUNTER_FILE, 'r') as f:
                data = json.load(f)
                if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
                    return data.get("count", 0)
    except:
        pass
    return 0


def increment_av_call_count():
    """Increment Alpha Vantage call counter"""
    try:
        count = get_av_call_count() + 1
        data = {"date": datetime.now().strftime("%Y-%m-%d"), "count": count}
        AV_COUNTER_FILE.parent.mkdir(exist_ok=True)
        with open(AV_COUNTER_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed to update AV counter: {e}")


def safe_request(url: str, timeout: int = API_TIMEOUT, retries: int = API_RETRIES) -> Optional[requests.Response]:
    """Safe HTTP request with timeout and retry handling"""
    for attempt in range(retries):
        try:
            resp = _request_session.get(url, timeout=timeout)
            return resp
        except requests.Timeout:
            logger.warning(f"Request timeout (attempt {attempt+1}/{retries}): {url[:50]}...")
            if attempt < retries - 1:
                import time
                time.sleep(2)
            else:
                return None
        except requests.ConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt+1}/{retries}): {str(e)[:50]}")
            if attempt < retries - 1:
                import time
                time.sleep(2)
            else:
                return None
    return None


def fetch_finnhub_news(ticker: str, limit: int = 10) -> List[Dict]:
    """Fetch news from Finnhub API"""
    if not FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not set")
        return []

    try:
        url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from=2026-01-01&to=2026-12-31&token={FINNHUB_API_KEY}"
        resp = safe_request(url)
        if resp is None:
            return []

        resp.raise_for_status()
        data = resp.json()

        news = []
        for item in data[:limit]:
            news.append({
                "headline": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "source": item.get("source", ""),
                "url": item.get("url", ""),
                "timestamp": item.get("datetime", 0)
            })

        logger.info(f"Finnhub: {len(news)} news items for {ticker}")
        return news
    except Exception as e:
        logger.error(f"Finnhub error: {e}")
        return []


def fetch_finnhub_sentiment(ticker: str) -> Optional[float]:
    """
    Calculate sentiment score from Finnhub news headlines.

    Returns:
        Sentiment score 0-1 (0.5 = neutral), or None if unavailable
    """
    news = fetch_finnhub_news(ticker, limit=10)
    if not news:
        return None

    # Simple news sentiment based on headline keywords
    bullish_words = ["beat", "raise", "surge", "rally", "gain", "up", "buy", "bullish", "record", "growth"]
    bearish_words = ["miss", "cut", "drop", "fall", "loss", "down", "sell", "bearish", "warning", "layoff"]

    news_score = 0.5
    for item in news:
        headline = item.get("headline", "").lower()
        for word in bullish_words:
            if word in headline:
                news_score += 0.05
        for word in bearish_words:
            if word in headline:
                news_score -= 0.05

    news_score = max(0, min(1, news_score))  # Clamp to 0-1
    logger.info(f"Finnhub sentiment for {ticker}: {news_score:.2f}")
    return news_score


def fetch_alpha_vantage_sentiment(ticker: str) -> Optional[float]:
    """Fetch sentiment from Alpha Vantage (respects 25/day limit)"""
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("ALPHA_VANTAGE_API_KEY not set")
        return None

    # Check daily limit
    current_count = get_av_call_count()
    if current_count >= AV_DAILY_LIMIT:
        logger.warning(f"Alpha Vantage daily limit reached ({current_count}/{AV_DAILY_LIMIT})")
        return None

    try:
        url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
        resp = safe_request(url)
        if resp is None:
            return None

        increment_av_call_count()
        resp.raise_for_status()
        data = resp.json()

        feed = data.get("feed", [])
        if not feed:
            return None

        # Calculate average sentiment
        total_sentiment = 0
        count = 0
        for item in feed[:20]:
            for ts in item.get("ticker_sentiment", []):
                if ts.get("ticker") == ticker:
                    score = float(ts.get("ticker_sentiment_score", 0))
                    total_sentiment += score
                    count += 1

        if count == 0:
            return None

        avg_sentiment = total_sentiment / count
        # Normalize to 0-1 range (Alpha Vantage uses -1 to 1)
        normalized = (avg_sentiment + 1) / 2

        logger.info(f"Alpha Vantage sentiment for {ticker}: {normalized:.2f} (from {count} articles)")
        return normalized
    except Exception as e:
        logger.error(f"Alpha Vantage error: {e}")
        return None


def apply_meme_discount(ticker: str, score: float) -> float:
    """
    Apply meme-stock discount to sentiment score.
    Meme stocks have noisy sentiment from Reddit sarcasm/tribal signaling.

    Args:
        ticker: Stock ticker
        score: Raw sentiment score (0-1)

    Returns:
        Discounted score (0-1)
    """
    if ticker in MEME_TICKERS:
        raw_score = score
        discounted = score * 0.7
        logger.info(f"Meme discount applied to {ticker}: {raw_score:.2f} -> {discounted:.2f}")
        return discounted
    return score


def has_upcoming_earnings(ticker: str, hours_ahead: int = 48) -> bool:
    """
    Check if ticker has earnings within the next N hours.
    Uses Finnhub earnings calendar (free tier).

    Args:
        ticker: Stock ticker symbol
        hours_ahead: Number of hours to look ahead (default 48)

    Returns:
        True if earnings within specified window, False otherwise
    """
    global _earnings_cache

    # Check cache first
    if ticker in _earnings_cache:
        cached = _earnings_cache[ticker]
        cache_age_h = (datetime.now() - datetime.fromtimestamp(cached["timestamp"])).total_seconds() / 3600
        if cache_age_h < EARNINGS_CACHE_DURATION_H:
            logger.debug(f"[EARNINGS] {ticker}: using cached result (age: {cache_age_h:.1f}h)")
            return cached["has_earnings"]

    if not FINNHUB_API_KEY:
        logger.debug(f"[EARNINGS] {ticker}: FINNHUB_API_KEY not set")
        return False

    try:
        now = datetime.utcnow()
        from_date = now.strftime("%Y-%m-%d")
        to_date = (now + timedelta(hours=hours_ahead)).strftime("%Y-%m-%d")

        url = (f"https://finnhub.io/api/v1/calendar/earnings"
               f"?from={from_date}&to={to_date}&symbol={ticker}"
               f"&token={FINNHUB_API_KEY}")

        resp = safe_request(url, timeout=5)
        if resp is None:
            return False

        resp.raise_for_status()
        data = resp.json()

        earnings = data.get("earningsCalendar", [])
        has_earnings = bool(earnings)

        # Cache the result
        earnings_date = earnings[0].get("date") if earnings else None
        _earnings_cache[ticker] = {
            "has_earnings": has_earnings,
            "earnings_date": earnings_date,
            "timestamp": datetime.now().timestamp()
        }

        if has_earnings:
            logger.info(f"[EARNINGS] {ticker}: Earnings on {earnings_date} — applying {EARNINGS_MULTIPLIER}x multiplier")

        return has_earnings

    except Exception as e:
        logger.debug(f"[EARNINGS] Check failed for {ticker}: {e}")
        return False


def apply_earnings_multiplier(ticker: str, score: float) -> float:
    """
    Boost sentiment score if earnings are within 48h.

    Args:
        ticker: Stock ticker symbol
        score: Raw sentiment score (0-1)

    Returns:
        Boosted score (capped at 0.95) if earnings upcoming, otherwise original score
    """
    if has_upcoming_earnings(ticker):
        boosted = min(score * EARNINGS_MULTIPLIER, 0.95)  # Cap at 0.95
        logger.info(f"[EARNINGS] {ticker}: {score:.2f} -> {boosted:.2f} (earnings boost)")
        return boosted
    return score


def get_combined_sentiment(ticker: str) -> Dict:
    """
    Get combined sentiment score from all available sources.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dict with:
            - ticker: str
            - timestamp: str (ISO)
            - finnhub_sentiment: float or None
            - alpha_vantage_sentiment: float or None
            - news_count: int
            - meme_discounted: bool
            - combined: float (0-1)
            - passes_threshold: bool (>= 0.55)
    """
    result = {
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "finnhub_sentiment": None,
        "alpha_vantage_sentiment": None,
        "twitter_sentiment": None,
        "news_count": 0,
        "meme_discounted": False,
        "combined": 0.5,
        "passes_threshold": False
    }

    scores = []
    weights = []

    # Finnhub news sentiment (primary)
    finnhub_score = fetch_finnhub_sentiment(ticker)
    if finnhub_score is not None:
        result["finnhub_sentiment"] = finnhub_score
        result["news_count"] = 1  # We have news
        scores.append(finnhub_score)
        weights.append(0.35)

    # Alpha Vantage sentiment (fallback)
    av_score = fetch_alpha_vantage_sentiment(ticker)
    if av_score is not None:
        result["alpha_vantage_sentiment"] = av_score
        scores.append(av_score)
        weights.append(0.25)

    # Twitter sentiment via Apify
    tw_data = get_twitter_sentiment(ticker, max_tweets=30)
    if tw_data is not None:
        result["twitter_sentiment"] = tw_data["score"]
        scores.append(tw_data["score"])
        weights.append(0.40)

    # Weighted average
    if scores and weights:
        total_weight = sum(weights)
        combined = sum(s * w for s, w in zip(scores, weights)) / total_weight
    else:
        combined = 0.5

    # Apply meme discount
    if ticker in MEME_TICKERS:
        combined = apply_meme_discount(ticker, combined)
        result["meme_discounted"] = True

    result["combined"] = combined
    result["passes_threshold"] = combined >= 0.55

    logger.info(f"Combined sentiment for {ticker}: {combined:.2f}, passes={result['passes_threshold']}")
    return result
