#!/usr/bin/env python3
"""
Stock Hunter - Phase 3 (Stock Hunter)
Social Sentiment + News Scraping + Real API Integration

Uses:
- Finnhub: News + headlines
- Alpha Vantage: Sentiment scores
- Massive (Polygon): Stock prices
- Stocktwits: Social sentiment (free, no auth)

Configuration centralized in config.py
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import requests
from requests.exceptions import Timeout, ConnectionError, RequestException
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import centralized config
from config import (
    FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, MASSIVE_API_KEY,
    MEME_TICKERS, RISK_CAPS, STOCK_HUNTER_TICKERS
)

from utils.sentiment import apply_earnings_multiplier
from utils.position_sizer import size_position, get_circuit_breaker, update_bankroll
from strategies.sentiment_scorer import get_ai_stock_sentiment

from utils.logging_config import setup_logging

# Paper trading PnL tracker
try:
    from paper_trading.pnl_tracker import log_signal
    PAPER_TRADING_ENABLED = True
except ImportError:
    PAPER_TRADING_ENABLED = False

# Paper money tracker (virtual $100 balance)
try:
    from runner import paper_money
    PAPER_MONEY_ENABLED = True
except ImportError:
    PAPER_MONEY_ENABLED = False

load_dotenv()

logger = setup_logging(
    log_file_path='/opt/slimy/pm_updown_bot_bundle/logs/stock_hunter.log',
    verbose=os.getenv('VERBOSE', 'false').lower() == 'true'
)

# Global requests session with retry logic
_request_session = requests.Session()
API_TIMEOUT = 15  # seconds - increased from 10
API_RETRIES = 2

# Cache for API responses (avoid rate limits)
_price_cache = {}
_sentiment_cache = {}
CACHE_DURATION_S = 900  # 15 minutes

# Rate limiting
MASSIVE_DELAY_S = 15  # More conservative rate limiting (was 12s)
last_massive_call = 0

# Alpha Vantage daily call limit tracking
AV_DAILY_LIMIT = 25
AV_COUNTER_FILE = Path("/opt/slimy/pm_updown_bot_bundle/logs/av_counter.json")

def get_av_call_count():
    """Get today's Alpha Vantage call count"""
    try:
        if AV_COUNTER_FILE.exists():
            with open(AV_COUNTER_FILE, 'r') as f:
                data = json.load(f)
                # Check if counter is from today
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

# RISK_CAPS and STOCK_HUNTER_TICKERS now imported from config.py


def safe_request(url, timeout=API_TIMEOUT, retries=API_RETRIES):
    """Safe HTTP request with timeout and retry handling"""
    for attempt in range(retries):
        try:
            resp = _request_session.get(url, timeout=timeout)
            return resp
        except Timeout:
            logger.warning(f"Request timeout (attempt {attempt+1}/{retries}): {url[:50]}...")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise
        except ConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt+1}/{retries}): {str(e)[:50]}")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise
    return None


# MARKETAUX REMOVED - Free tier exhausted (402 Payment Required)
# Finnhub sentiment is sufficient as primary source
# Alpha Vantage News Sentiment as fallback when needed


def fetch_finnhub_news(ticker=None, limit=10):
    """Fetch news from Finnhub API"""
    if not FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not set")
        return []
    
    try:
        if ticker:
            url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from=2026-01-01&to=2026-12-31&token={FINNHUB_API_KEY}"
        else:
            url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
        
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
        
        logger.info(f"Finnhub: {len(news)} news items for {ticker or 'general'}")
        return news
    except Exception as e:
        logger.error(f"Finnhub error: {e}")
        return []


def fetch_alpha_vantage_sentiment(ticker):
    """Fetch sentiment from Alpha Vantage (respects 25/day limit)"""
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("ALPHA_VANTAGE_API_KEY not set")
        return None
    
    # Check daily limit
    current_count = get_av_call_count()
    if current_count >= AV_DAILY_LIMIT:
        logger.warning(f"[STOCK_HUNTER] Alpha Vantage daily limit reached ({current_count}/{AV_DAILY_LIMIT})")
        return None
    
    try:
        url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
        resp = safe_request(url)
        if resp is None:
            return None
        
        # Increment counter after successful request
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
        
        avg_sentiment = total_sentiment / count if count > 0 else 0
        # Normalize to 0-1 range (Alpha Vantage uses -1 to 1)
        normalized = (avg_sentiment + 1) / 2
        
        logger.info(f"Alpha Vantage: {ticker} sentiment = {normalized:.2f} (from {count} articles)")
        return normalized
    except Exception as e:
        logger.error(f"Alpha Vantage error: {e}")
        return None


def fetch_alpha_vantage_price(ticker):
    """Fetch stock price from Alpha Vantage (backup - limited to 25/day)"""
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("ALPHA_VANTAGE_API_KEY not set")
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
            return {"close": price, "source": "alpha_vantage"}
        return None
    except Exception as e:
        logger.error(f"Alpha Vantage error for {ticker}: {e}")
        return None


def fetch_massive_price(ticker):
    """Fetch stock price from Massive (Polygon) API - with rate limiting and caching"""
    global last_massive_call
    
    # Check cache first
    cache_key = f"price_{ticker}"
    if cache_key in _price_cache:
        cached = _price_cache[cache_key]
        if time.time() - cached["timestamp"] < CACHE_DURATION_S:
            logger.info(f"Using cached price for {ticker}: ${cached['price']['close']:.2f}")
            return cached["price"]
    
    if not MASSIVE_API_KEY:
        logger.warning("MASSIVE_API_KEY not set")
        return fetch_alpha_vantage_price(ticker)
    
    # Rate limiting - ensure 15s between calls (reduced from 12s for safety)
    elapsed = time.time() - last_massive_call
    if elapsed < MASSIVE_DELAY_S:
        wait_time = MASSIVE_DELAY_S - elapsed
        logger.debug(f"Rate limiting: waiting {wait_time:.1f}s before Massive call")
        time.sleep(wait_time)
    
    try:
        url = f"https://api.massive.com/v2/aggs/ticker/{ticker}/prev?apiKey={MASSIVE_API_KEY}"
        resp = safe_request(url)
        if resp is None:
            logger.warning(f"Massive request failed for {ticker}, falling back to Alpha Vantage")
            return fetch_alpha_vantage_price(ticker)
        
        last_massive_call = time.time()
        
        # Handle rate limiting
        if resp.status_code == 429:
            logger.warning(f"Massive API rate limited for {ticker}, falling back to Alpha Vantage")
            return fetch_alpha_vantage_price(ticker)
        
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("status") == "OK" and data.get("results"):
            result = data["results"][0]
            price = {
                "open": result.get("o"),
                "high": result.get("h"),
                "low": result.get("l"),
                "close": result.get("c"),
                "volume": result.get("v"),
                "vwap": result.get("vw"),
                "source": "massive"
            }
            # Cache the result
            _price_cache[cache_key] = {"price": price, "timestamp": time.time()}
            logger.info(f"Massive: {ticker} = ${price['close']:.2f}")
            return price
        return None
    except Exception as e:
        logger.error(f"Massive error for {ticker}: {e}")
        # Try Alpha Vantage as fallback
        return fetch_alpha_vantage_price(ticker)


def fetch_stocktwits_sentiment(ticker):
    """Fetch social sentiment from Stocktwits (DISABLED - 403 Forbidden)"""
    # Stocktwits has blocked unauthenticated access
    # Return None to use other sentiment sources
    return None


def analyze_ticker(ticker):
    """Analyze a single ticker using all available APIs"""
    logger.info(f"Analyzing {ticker}...")
    
    # Fetch data from all sources with individual try/catch
    price_data = None
    av_sentiment = None
    news = []
    
    try:
        price_data = fetch_massive_price(ticker)
    except Exception as e:
        logger.error(f"Price fetch failed for {ticker}: {e}")
    
    # MARKETAUX REMOVED - Free tier exhausted
    # Finnhub is now primary, Alpha Vantage as fallback when Finnhub < 3 articles
    
    try:
        news = fetch_finnhub_news(ticker, limit=5)
    except Exception as e:
        logger.error(f"Finnhub news failed for {ticker}: {e}")
    
    # FIXED: Always call Alpha Vantage to get real per-ticker sentiment scores
    # The old logic only called AV as fallback when Finnhub had < 3 articles,
    # which resulted in all tickers getting similar scores
    av_sentiment = None
    av_fallback_status = "not called"
    try:
        av_sentiment = fetch_alpha_vantage_sentiment(ticker)
        if av_sentiment is not None:
            av_fallback_status = "success"
        else:
            count = get_av_call_count()
            av_fallback_status = "skipped" if count >= AV_DAILY_LIMIT else "failed"
    except Exception as e:
        logger.error(f"Alpha Vantage sentiment failed for {ticker}: {e}")
        av_fallback_status = "error"

    logger.info(f"[STOCK_HUNTER] {ticker}: Finnhub={len(news)} articles, AV sentiment={av_fallback_status}")
    
    # Build analysis result
    result = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "price": price_data.get("close") if price_data else None,
        "volume": price_data.get("volume") if price_data else None,
    }
    
    # Calculate combined sentiment score
    # Alpha Vantage is PRIMARY - provides real per-ticker sentiment scores
    # Finnhub is SECONDARY - provides news buzz indicator via keyword analysis
    scores = []
    weights = []

    # Alpha Vantage news sentiment (PRIMARY - real ticker-specific sentiment)
    if av_sentiment is not None:
        scores.append(av_sentiment)
        weights.append(0.70)  # AV is primary - gives real variation per ticker
        result["alpha_vantage_sentiment"] = av_sentiment
        logger.info(f"[SENTIMENT] {ticker}: Alpha Vantage score={av_sentiment:.3f}")

    # Finnhub news sentiment analysis (SECONDARY - buzz indicator)
    if news:
        # Simple news sentiment based on headline keywords
        bullish_words = ["beat", "raise", "surge", "rally", "gain", "up", "buy", "bullish"]
        bearish_words = ["miss", "cut", "drop", "fall", "loss", "down", "sell", "bearish"]

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
        scores.append(news_score)
        weights.append(0.30)  # Finnhub is secondary
        result["news_count"] = len(news)
        result["news_score"] = news_score
        logger.info(f"[SENTIMENT] {ticker}: Finnhub news_score={news_score:.3f} ({len(news)} articles)")

    # Weighted average - ONLY calculate if we have real data
    # FIXED: Don't default to 0.5 when no data - return None to indicate no signal
    if scores and weights:
        total_weight = sum(weights)
        combined = sum(s * w for s, w in zip(scores, weights)) / total_weight
    else:
        # No sentiment data available - don't use a fake default
        combined = None
        logger.warning(f"[SENTIMENT] {ticker}: NO sentiment data from any source!")
    
    # MEME-STOCK DISCOUNT (Priority 1D)
    # Apply 0.7x multiplier to meme tickers to reduce false positive signals
    # GME, AMC, etc. have noisy sentiment from Reddit sarcasm/tribal signaling
    if ticker in MEME_TICKERS:
        raw_sentiment = combined
        combined = combined * 0.7
        result["meme_discount"] = True
        logger.info(f"[STOCK_HUNTER] {ticker}: meme-stock discount applied. Raw={raw_sentiment:.2f} -> Adjusted={combined:.2f}")
        logger.debug(f"Applied meme-stock discount to {ticker}")

    # EARNINGS CALENDAR MULTIPLIER (Priority 1E)
    # Apply 1.20x boost if earnings within 48 hours (higher volatility = more actionable)
    if combined is not None:
        raw_sentiment = combined
        combined = apply_earnings_multiplier(ticker, combined)
        if combined != raw_sentiment:
            result["earnings_boost"] = True
            logger.info(f"[STOCK_HUNTER] {ticker}: earnings boost applied. After meme={raw_sentiment:.2f} -> Final={combined:.2f}")

    # AI ENHANCEMENT (Priority 1F) — Grok/GLM cascade blends with Finnhub/AV score
    # Skipped if no Finnhub/AV data available (combined is None)
    if combined is not None:
        headlines = [n.get("headline", "") for n in news if n.get("headline")]
        raw_before_ai = combined
        combined = get_ai_stock_sentiment(ticker, combined, headlines, blend_weight=0.5)
        if combined != raw_before_ai:
            result["ai_enhanced"] = True
            logger.info(f"[STOCK_HUNTER] {ticker}: AI enhanced. Finnhub/AV={raw_before_ai:.3f} -> "
                       f"AI-blended={combined:.3f}")

    result["combined_sentiment"] = combined
    # Only pass threshold if we have valid sentiment data
    result["passes_threshold"] = (combined is not None and combined >= RISK_CAPS.get("stock_sentiment_threshold", 0.55))

    if combined is not None:
        logger.info(f"  {ticker}: combined sentiment = {combined:.2f}, passes = {result['passes_threshold']}")
    else:
        logger.info(f"  {ticker}: NO SENTIMENT DATA - cannot pass threshold")

    return result


def calculate_kelly_position_size(sentiment: float, max_pos_usd: float) -> float:
    """
    Kelly criterion position sizing based on sentiment confidence
    
    Scales position size by confidence:
    - 0.50-0.55: 25% of max position (marginal)
    - 0.55-0.60: 50% of max position
    - 0.60-0.65: 75% of max position
    - 0.65+: 100% of max position (strong conviction)
    
    Args:
        sentiment: Combined sentiment score (0-1)
        max_pos_usd: Maximum position size in USD
    
    Returns:
        Position size in USD
    """
    if sentiment >= 0.65:
        multiplier = 1.0
    elif sentiment >= 0.60:
        multiplier = 0.75
    elif sentiment >= 0.55:
        multiplier = 0.50
    elif sentiment >= 0.50:
        multiplier = 0.25
    else:
        multiplier = 0.0  # Below threshold
    
    return max_pos_usd * multiplier


def optimize_stock_hunter_strategy(bankroll, max_pos_usd, mode="shadow"):
    """Main function for Phase 3: Stock Hunter"""
    logger.info("=" * 60)
    logger.info("PHASE 3: STOCK HUNTER (REAL APIs)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info(f"Target tickers: {STOCK_HUNTER_TICKERS}")
    logger.info("=" * 60)
    
    # Analyze all target tickers (PARALLEL for speed)
    results = []
    
    def analyze_with_delay(ticker):
        """Analyze ticker with small delay to prevent rate limits"""
        result = analyze_ticker(ticker)
        return result
    
    # Use ThreadPoolExecutor for parallel API calls
    # Max 3 workers to avoid rate limiting issues
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_ticker = {executor.submit(analyze_with_delay, ticker): ticker 
                           for ticker in STOCK_HUNTER_TICKERS}
        
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                analysis = future.result()
                if analysis:
                    results.append(analysis)
            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {e}")
    
    logger.info(f"Parallel analysis complete: {len(results)} tickers in <20s")
    
    # Filter to passing stocks
    passing = [r for r in results if r.get("passes_threshold")]
    
    logger.info("=" * 60)
    logger.info("RESULTS")
    logger.info("=" * 60)
    logger.info(f"Tickers analyzed: {len(results)}")
    logger.info(f"Passing threshold: {len(passing)}")
    
    # Sort by combined sentiment
    passing.sort(key=lambda x: x.get("combined_sentiment", 0), reverse=True)
    
    # Update circuit breaker with current bankroll
    update_bankroll(bankroll)

    # Get current open positions
    try:
        from runner import paper_money
        current_positions = len(paper_money.positions) if hasattr(paper_money, 'positions') else 0
    except Exception:
        current_positions = 0

    # Generate orders with Kelly criterion sizing
    orders = []
    for stock in passing[:5]:  # Top 5 only
        ticker = stock.get("ticker")
        price = stock.get("price")
        sentiment = stock.get("combined_sentiment", 0)

        if not price:
            logger.warning(f"Skipping {ticker}: no price data")
            continue

        # Use position_sizer for Kelly criterion with EV filter
        # For stocks: sentiment = confidence, price = stock price
        kelly_size, sizing_meta = size_position(
            market_id=f"stock_{ticker}",
            market_price=0.5,  # neutral baseline: edge = sentiment - 0.5
            bankroll=bankroll,
            current_positions=current_positions,
            estimated_prob=sentiment,
            odds=price,
            fees_pct=0.0,
        )

        if sizing_meta.get("blocked"):
            logger.info(f"Skipping {ticker}: {sizing_meta.get('block_reason', 'blocked')}")
            continue

        # Track positions for next iteration
        current_positions += 1
        
        # Calculate multiplier based on max position
        kelly_mult = sizing_meta.get("kelly", {}).get("kelly_fraction", 0) if kelly_size > 0 else 0

        order = {
            "ticker": ticker,
            "side": "buy",
            "size": round(kelly_size, 2),
            "price": price,
            "sentiment": round(sentiment, 2),
            "kelly_multiplier": kelly_mult,
            "position_meta": sizing_meta,
            "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        orders.append(order)
        logger.info(f"Order: {ticker} ${kelly_size:.2f} @ ${price:.2f} (sentiment: {order['sentiment']:.2f}, kelly: {order['kelly_multiplier']:.0%})")
        
        # Execute paper money trade (virtual $100 account)
        # BUG FIX: Check max positions AND duplicate tickers before buying
        if mode == "shadow" and PAPER_MONEY_ENABLED:
            try:
                # Get current positions to check limits
                current_positions = paper_money.positions
                max_positions = RISK_CAPS.get("stock_max_open_pos", 3)
                
                # Check if already holding this ticker (duplicate check)
                existing_tickers = [p["ticker"] for p in current_positions]
                if ticker in existing_tickers:
                    logger.warning(f"[STOCK_HUNTER] Skipping {ticker} - already in portfolio")
                elif len(current_positions) >= max_positions:
                    logger.warning(f"[STOCK_HUNTER] Skipping {ticker} - max positions ({max_positions}) reached")
                else:
                    # Pass sentiment to execute_buy
                    paper_money.execute_buy("stock", ticker, kelly_size, price, sentiment=sentiment)
            except Exception as e:
                logger.warning(f"Paper money trade failed: {e}")
        
        # Log to paper trading PnL tracker (for shadow mode)
        if mode == "shadow" and PAPER_TRADING_ENABLED:
            try:
                signal_id = log_signal(ticker, sentiment, price, kelly_size)
                logger.debug(f"Logged paper trade signal #{signal_id} for {ticker}")
            except Exception as e:
                logger.warning(f"Failed to log paper trade: {e}")
    
    # Summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total orders: {len(orders)}")
    if orders:
        logger.info(f"Top pick: {orders[0]['ticker']} (sentiment: {orders[0]['sentiment']:.2f})")
    
    # Generate proof (inline to avoid circular import)
    proof_id = f"phase3_stock_hunter_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_path = Path(f"/opt/slimy/pm_updown_bot_bundle/proofs/{proof_id}.json")
    proof_path.parent.mkdir(exist_ok=True)
    
    proof_data = {
        "mode": mode,
        "bankroll": bankroll,
        "max_pos_usd": max_pos_usd,
        "data": {
            "results": results,
            "orders": orders
        },
        "risk_caps": RISK_CAPS,
        "apis": {
            "finnhub": bool(FINNHUB_API_KEY),
            "alpha_vantage": bool(ALPHA_VANTAGE_API_KEY),
            "massive": bool(MASSIVE_API_KEY)
            # marketaux: REMOVED - free tier exhausted
        },
        "meme_tickers": list(MEME_TICKERS)
    }
    
    with open(proof_path, 'w') as f:
        json.dump(proof_data, f, indent=2)
    logger.info(f"Proof: {proof_id}")
    
    return len(orders)


def main(mode="shadow", bankroll=100.0, max_pos_usd=10.0, verbose=False, risk_caps=None):
    """Main entry point - accepts parameters directly to avoid argparse conflicts
    
    Args:
        mode: Execution mode (shadow, micro-live, real-live)
        bankroll: Starting bankroll in USD
        max_pos_usd: Maximum position size
        verbose: Enable debug logging
        risk_caps: Dict of risk parameters. If None, uses module default.
    """
    global RISK_CAPS  # Allow updating the global
    
    # FIXED: Use passed risk_caps instead of hardcoded module values
    if risk_caps is not None:
        # Update global RISK_CAPS with passed values
        RISK_CAPS.update(risk_caps)
        logger.info(f"Using passed RISK_CAPS: stock_sentiment_threshold={RISK_CAPS.get('stock_sentiment_threshold')}, edge_after_fees_pct={RISK_CAPS.get('edge_after_fees_pct')}")
    
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    result = optimize_stock_hunter_strategy(
        mode=mode,
        bankroll=bankroll,
        max_pos_usd=max_pos_usd
    )
    
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stock Hunter - Phase 3")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--max-pos", type=float, default=10.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    sys.exit(main(mode=args.mode, bankroll=args.bankroll, max_pos_usd=args.max_pos, verbose=args.verbose))
