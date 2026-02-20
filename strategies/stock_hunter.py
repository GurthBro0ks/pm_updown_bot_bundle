#!/usr/bin/env python3
"""
Stock Hunter - Phase 3 (Stock Hunter)
Social Sentiment + News Scraping + Real API Integration

Uses:
- Finnhub: News + headlines
- Alpha Vantage: Sentiment scores
- Massive (Polygon): Stock prices
- Stocktwits: Social sentiment (free, no auth)
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

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

from utils.logging_config import setup_logging

load_dotenv()

logger = setup_logging(
    log_file_path='/opt/slimy/pm_updown_bot_bundle/logs/stock_hunter.log',
    verbose=os.getenv('VERBOSE', 'false').lower() == 'true'
)

# API Keys
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
ALPHA_VANTAGE_API_KEY = os.getenv('ALPHA_VANTAGE_API_KEY')
MASSIVE_API_KEY = os.getenv('MASSIVE_API_KEY')
MARKETAUX_API_KEY = os.getenv('MARKETAUX_API_KEY')

# Risk Caps
RISK_CAPS = {
    "max_pos_usd": 100,
    "max_daily_loss_usd": 30,
    "max_open_pos": 3,
    "max_daily_positions": 10,
    "min_liquidity_usd": 10000,
    "edge_after_fees_pct": 1.0,
    "sentiment_threshold": 0.5,  # Lower threshold for real data
}

# Target tickers to analyze
TARGET_TICKERS = ["AAPL", "TSLA", "NVDA", "GME", "AMC", "META", "AMZN", "GOOGL", "MSFT", "AMD"]


def fetch_marketaux_sentiment(ticker):
    """Fetch news + sentiment from Marketaux"""
    if not MARKETAUX_API_KEY:
        logger.warning("MARKETAUX_API_KEY not set")
        return None
    
    try:
        url = f"https://api.marketaux.com/v1/news/all?api_token={MARKETAUX_API_KEY}&symbols={ticker}&limit=10"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        articles = data.get("data", [])
        if not articles:
            return None
        
        # Calculate sentiment from entity sentiment scores
        total_sentiment = 0
        count = 0
        for article in articles:
            entities = article.get("entities", [])
            for entity in entities:
                if entity.get("symbol") == ticker:
                    score = entity.get("sentiment_score")
                    if score is not None:
                        total_sentiment += score
                        count += 1
        
        # Normalize to 0-1 range (Marketaux uses -1 to 1)
        avg_sentiment = total_sentiment / count if count > 0 else 0
        normalized = (avg_sentiment + 1) / 2
        
        result = {
            "sentiment_score": normalized,
            "articles_count": len(articles),
            "entities_matched": count,
            "raw_sentiment": avg_sentiment
        }
        
        logger.info(f"Marketaux: {ticker} sentiment = {normalized:.2f} (from {count} mentions in {len(articles)} articles)")
        return result
    except Exception as e:
        logger.error(f"Marketaux error for {ticker}: {e}")
        return None


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
        
        resp = requests.get(url, timeout=10)
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
    """Fetch sentiment from Alpha Vantage"""
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("ALPHA_VANTAGE_API_KEY not set")
        return None
    
    try:
        url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
        resp = requests.get(url, timeout=10)
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


def fetch_massive_price(ticker):
    """Fetch stock price from Massive (Polygon) API"""
    if not MASSIVE_API_KEY:
        logger.warning("MASSIVE_API_KEY not set")
        return None
    
    try:
        url = f"https://api.massive.com/v2/aggs/ticker/{ticker}/prev?apiKey={MASSIVE_API_KEY}"
        resp = requests.get(url, timeout=10)
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
                "vwap": result.get("vw")
            }
            logger.info(f"Massive: {ticker} = ${price['close']:.2f}")
            return price
        return None
    except Exception as e:
        logger.error(f"Massive error for {ticker}: {e}")
        return None


def fetch_stocktwits_sentiment(ticker):
    """Fetch social sentiment from Stocktwits (DISABLED - 403 Forbidden)"""
    # Stocktwits has blocked unauthenticated access
    # Return None to use other sentiment sources
    return None


def analyze_ticker(ticker):
    """Analyze a single ticker using all available APIs"""
    logger.info(f"Analyzing {ticker}...")
    
    # Fetch data from all sources
    price_data = fetch_massive_price(ticker)
    marketaux = fetch_marketaux_sentiment(ticker)
    av_sentiment = fetch_alpha_vantage_sentiment(ticker)
    news = fetch_finnhub_news(ticker, limit=5)
    
    # Build analysis result
    result = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "price": price_data.get("close") if price_data else None,
        "volume": price_data.get("volume") if price_data else None,
    }
    
    # Calculate combined sentiment score
    scores = []
    weights = []
    
    if marketaux:
        scores.append(marketaux.get("sentiment_score", 0.5))
        weights.append(0.35)  # Marketaux weighted high (news + sentiment)
        result["marketaux"] = marketaux
    
    if av_sentiment is not None:
        scores.append(av_sentiment)
        weights.append(0.35)  # Alpha Vantage news sentiment
        result["alpha_vantage_sentiment"] = av_sentiment
    
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
        weights.append(0.2)
        result["news_count"] = len(news)
        result["news_score"] = news_score
    
    # Weighted average
    if scores and weights:
        total_weight = sum(weights)
        combined = sum(s * w for s, w in zip(scores, weights)) / total_weight
    else:
        combined = 0.5
    
    result["combined_sentiment"] = combined
    result["passes_threshold"] = combined >= RISK_CAPS["sentiment_threshold"]
    
    logger.info(f"  {ticker}: combined sentiment = {combined:.2f}, passes = {result['passes_threshold']}")
    
    return result


def optimize_stock_hunter_strategy(bankroll, max_pos_usd, mode="shadow"):
    """Main function for Phase 3: Stock Hunter"""
    logger.info("=" * 60)
    logger.info("PHASE 3: STOCK HUNTER (REAL APIs)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info(f"Target tickers: {TARGET_TICKERS}")
    logger.info("=" * 60)
    
    # Analyze all target tickers
    results = []
    for ticker in TARGET_TICKERS:
        try:
            analysis = analyze_ticker(ticker)
            results.append(analysis)
            time.sleep(0.5)  # Rate limiting
        except Exception as e:
            logger.error(f"Error analyzing {ticker}: {e}")
    
    # Filter to passing stocks
    passing = [r for r in results if r.get("passes_threshold")]
    
    logger.info("=" * 60)
    logger.info("RESULTS")
    logger.info("=" * 60)
    logger.info(f"Tickers analyzed: {len(results)}")
    logger.info(f"Passing threshold: {len(passing)}")
    
    # Sort by combined sentiment
    passing.sort(key=lambda x: x.get("combined_sentiment", 0), reverse=True)
    
    # Calculate optimal order size
    if len(passing) > 0:
        optimal_size = min(bankroll / len(passing), max_pos_usd)
    else:
        optimal_size = 0.0
    
    # Generate orders
    orders = []
    for stock in passing[:5]:  # Top 5 only
        ticker = stock.get("ticker")
        price = stock.get("price")
        
        if not price:
            logger.warning(f"Skipping {ticker}: no price data")
            continue
        
        order = {
            "ticker": ticker,
            "side": "buy",
            "size": round(optimal_size, 2),
            "price": price,
            "sentiment": round(stock.get("combined_sentiment", 0), 2),
            "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        orders.append(order)
        logger.info(f"Order: {ticker} ${optimal_size:.2f} @ ${price:.2f} (sentiment: {order['sentiment']:.2f})")
    
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
            "massive": bool(MASSIVE_API_KEY),
            "marketaux": bool(MARKETAUX_API_KEY)
        }
    }
    
    with open(proof_path, 'w') as f:
        json.dump(proof_data, f, indent=2)
    logger.info(f"Proof: {proof_id}")
    
    return len(orders)


def main():
    parser = argparse.ArgumentParser(description="Stock Hunter - Phase 3")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--max-pos", type=float, default=10.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    result = optimize_stock_hunter_strategy(
        mode=args.mode,
        bankroll=args.bankroll,
        max_pos_usd=args.max_pos
    )
    
    return result


if __name__ == "__main__":
    sys.exit(main())
