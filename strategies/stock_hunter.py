#!/usr/bin/env python3
"""
Stock Hunter - Phase 3 (Stock Hunter)
Social Sentiment + News Scraping + Unusual Options Activity + Penny Stock Screener
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

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import simple logging config
from utils.logging_config import setup_logging

load_dotenv()

# Setup logging (no syntax errors)
logger = setup_logging(
    log_file_path='/opt/slimy/pm_updown_bot_bundle/logs/stock_hunter.log',
    verbose=os.getenv('VERBOSE', 'false').lower() == 'true'
)

# Risk Caps for Phase 3 (Stock Hunter)
RISK_CAPS = {
    "max_pos_usd": 100,  # Phase 3: Higher limit for penny stocks
    "max_daily_loss_usd": 30,  # Phase 3: Lower loss limit (stocks are volatile)
    "max_open_pos": 3,  # Phase 3: Fewer concurrent positions
    "max_daily_positions": 10,  # Phase 3: Fewer daily positions (quality)
    "min_liquidity_usd": 10000,  # Phase 3: Higher minimum liquidity
    "edge_after_fees_pct": 1.0,  # Phase 3: Higher edge threshold (stocks have fees)
    "sentiment_threshold": 0.6,  # Phase 3: Bullish sentiment threshold
    # Screener caps (flat structure for backward compatibility)
    "min_market_cap_usd": 10000000,  # Phase 3: $10M minimum market cap (penny stocks)
    "max_market_cap_usd": 300000000,  # Phase 3: $300M maximum (small caps)
    "min_price_usd": 1.0,  # Phase 3: Minimum $1 price (not penny stocks)
    "max_price_usd": 5.0,  # Phase 3: Maximum $5 price (small caps)
    # Options caps
    "min_volume_spike": 2.0,
    "min_oi_spike": 1.5,
    "min_iv_spike": 1.3,
}

# Stock Sources Configuration
STOCK_SOURCES = {
    "reddit": {
        "name": "Reddit",
        "subreddits": ["wallstreetbets", "stocks", "pennystocks", "stockmarket"],
        "min_mentions_per_hour": 10,
        "min_sentiment_score": 0.7
    },
    "twitter": {
        "name": "X/Twitter",
        "min_mentions_per_hour": 20,
        "min_sentiment_score": 0.7
    },
    "news": {
        "name": "News Sources",
        "sources": ["finnhub", "alpha_vantage", "stocknewsapi"],
        "min_news_per_hour": 5,
        "sentiment_keywords": ["beat", "missed", "raised", "downgraded"]
    },
    "options": {
        "name": "Unusual Options Activity",
        "min_volume_spike": 2.0,  # 2x normal volume
        "min_oi_spike": 2.0,  # 2x normal open interest
        "min_iv_spike": 2.0  # 2x normal implied volatility
    },
    "screener": {
        "name": "Penny Stock Screener",
        "min_volume_usd": 100000,  # $100K/day minimum volume
        "max_spread_pct": 0.5,  # 0.5% bid-ask spread
        "min_price": 1.0,
        "max_price": 5.0
    }
}

def calculate_sentiment_score(mentions, sentiment_scores, volume):
    """
    Calculate sentiment score from social media mentions
    Simple model: more bullish mentions + higher volume = higher score
    """
    if not mentions:
        return 0.0
    
    # Normalize sentiment scores (assuming range -1 to 1)
    avg_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0
    volume_score = min(volume / 1000.0, 1.0)  # Normalize to 0-1 range
    
    # Combined score (weighted average)
    sentiment_weight = 0.7
    volume_weight = 0.3
    
    sentiment_score = (avg_sentiment * sentiment_weight) + (volume_score * volume_weight)
    logger.debug(f"Sentiment Score: avg={avg_sentiment:.2f}, volume={volume_score:.2f}, combined={sentiment_score:.2f}")
    
    return sentiment_score

def analyze_news_sentiment(news_headlines, keywords):
    """
    Analyze news sentiment based on keywords
    Returns bullish/bearish/neutral score
    """
    if not news_headlines:
        return 0.0
    
    bullish_count = 0
    bearish_count = 0
    
    for headline in news_headlines:
        headline_lower = headline.lower()
        for keyword in keywords.get("bullish", []):
            if keyword in headline_lower:
                bullish_count += 1
                break
        for keyword in keywords.get("bearish", []):
            if keyword in headline_lower:
                bearish_count += 1
                break
    
    # Calculate sentiment (-1 to 1)
    total = bullish_count + bearish_count
    if total == 0:
        return 0.0
    elif bullish_count > bearish_count:
        return (bullish_count - bearish_count) / total  # Positive = bullish
    else:
        return (bearish_count - bullish_count) / total  # Negative = bearish

def detect_unusual_options_activity(ticker, volume, oi, iv):
    """
    Detect unusual options activity
    Volume spike, OI spike, IV spike
    """
    # Mock data (would fetch from API)
    historical_volume = 100000  # Normal daily volume
    historical_oi = 1000  # Normal open interest
    historical_iv = 0.25  # Normal implied volatility (25%)
    
    # Calculate ratios
    volume_ratio = volume / historical_volume if historical_volume > 0 else 0.0
    oi_ratio = oi / historical_oi if historical_oi > 0 else 0.0
    iv_ratio = iv / historical_iv if historical_iv > 0 else 0.0
    
    # Check thresholds
    unusual_flags = []
    if volume_ratio >= RISK_CAPS["min_volume_spike"]:
        unusual_flags.append("volume_spike")
        logger.info(f"Unusual Volume: {ticker} ({volume_ratio:.1f}x normal)")
    if oi_ratio >= RISK_CAPS["min_oi_spike"]:
        unusual_flags.append("oi_spike")
        logger.info(f"Unusual OI: {ticker} ({oi_ratio:.1f}x normal)")
    if iv_ratio >= RISK_CAPS["min_iv_spike"]:
        unusual_flags.append("iv_spike")
        logger.info(f"Unusual IV: {ticker} ({iv_ratio:.1f}x normal)")
    
    unusual_score = len(unusual_flags) / 3  # Normalize to 0-1
    return unusual_score

def screen_penny_stocks(ticker, price, market_cap, volume, bid_spread):
    """
    Screen penny stocks based on liquidity, spread, price, market cap
    Returns True if stock passes screener, False otherwise
    """
    # Market cap check
    if market_cap < RISK_CAPS["min_market_cap_usd"]:
        logger.debug(f"Skipping {ticker}: market cap ${market_cap/1000000:.0f}B < ${RISK_CAPS['min_market_cap_usd']/1000000:.0f}B")
        return False
    if market_cap > RISK_CAPS["max_market_cap_usd"]:
        logger.debug(f"Skipping {ticker}: market cap ${market_cap/1000000:.0f}B > ${RISK_CAPS['max_market_cap_usd']/1000000:.0f}B")
        return False
    
    # Price check
    if price < RISK_CAPS["min_price_usd"] or price > RISK_CAPS["max_price_usd"]:
        logger.debug(f"Skipping {ticker}: price ${price:.2f} outside range ${RISK_CAPS['min_price_usd']:.2f}-${RISK_CAPS['max_price_usd']:.2f}")
        return False
    
    # Volume check
    if volume < RISK_CAPS["min_liquidity_usd"]:
        logger.debug(f"Skipping {ticker}: volume ${volume:.0f} < ${RISK_CAPS['screener']['min_volume_usd']:.0f}")
        return False
    
    # Spread check
    if bid_spread > RISK_CAPS["screener"]["max_spread_pct"]:
        logger.debug(f"Skipping {ticker}: spread {bid_spread:.1f}% > {RISK_CAPS['screener']['max_spread_pct']}%")
        return False
    
    logger.info(f"Passed screener: {ticker} (cap: ${market_cap/1000000:.0f}B, price: ${price:.2f}, volume: ${volume:.0f}, spread: {bid_spread:.1f}%)")
    return True

def find_best_stocks(stock_data, min_score=0.6):
    """
    Find best stocks based on sentiment, news, unusual activity
    """
    best_stocks = []
    
    for stock in stock_data:
        ticker = stock.get("ticker", "")
        sentiment_score = stock.get("sentiment_score", 0.0)
        news_score = stock.get("news_score", 0.0)
        unusual_score = stock.get("unusual_score", 0.0)
        
        # Combined score (sentiment + news + unusual activity)
        # Higher is better for trading
        combined_score = (sentiment_score * 0.4) + (news_score * 0.4) + (unusual_score * 0.2)
        
        if combined_score >= min_score:
            if screen_penny_stocks(
                ticker=ticker,
                price=stock.get("price", 1.0),
                market_cap=stock.get("market_cap", 0),
                volume=stock.get("volume", 0),
                bid_spread=stock.get("bid_spread", 0)
            ):
                best_stocks.append(stock)
                logger.debug(f"Best stock: {ticker} (combined: {combined_score:.2f}, sentiment: {sentiment_score:.2f}, news: {news_score:.2f}, unusual: {unusual_score:.2f})")
    
    logger.info(f"Found {len(best_stocks)} best stocks (score >= {min_score})")
    return best_stocks

def optimize_stock_hunter_strategy(bankroll, max_pos_usd, mode="shadow"):
    """
    Main function for Phase 3: Stock Hunter
    """
    logger.info("=" * 60)
    logger.info("PHASE 3: STOCK HUNTER")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
    # Mock stock data (would fetch from APIs)
    mock_stock_data = [
        {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "price": 1.75,
            "market_cap": 2750000000000,
            "volume": 50000000,
            "bid_spread": 0.01,
            "sentiment_score": 0.65,  # Moderate bullish
            "news_score": 0.70,  # Slightly bullish (beat earnings)
            "unusual_score": 0.30  # Slightly elevated options activity
        },
        {
            "ticker": "TSLA",
            "name": "Tesla, Inc.",
            "price": 0.85,
            "market_cap": 2750000000000,
            "volume": 120000000,
            "bid_spread": 0.02,
            "sentiment_score": 0.80,  # Very bullish (Reddit hype)
            "news_score": 0.85,  # Very bullish (beat expectations)
            "unusual_score": 0.45  # Elevated options activity (EV calls)
        },
        {
            "ticker": "NVDA",
            "name": "NVIDIA Corporation",
            "price": 2.50,
            "market_cap": 2200000000000,
            "volume": 80000000,
            "bid_spread": 0.01,
            "sentiment_score": 0.90,  # Extremely bullish (AI boom)
            "news_score": 0.80,  # Bullish (beat estimates)
            "unusual_score": 0.20  # Normal options activity
        },
        {
            "ticker": "GME",
            "name": "GameStop Corp.",
            "price": 1.20,
            "market_cap": 13000000000,
            "volume": 5000000,
            "bid_spread": 0.05,
            "sentiment_score": 0.85,  # Very bullish (Reddit meme stock)
            "news_score": 0.60,  # Neutral
            "unusual_score": 0.80  # Extremely elevated (Reddit hype, short squeeze)
        },
        {
            "ticker": "AMC",
            "name": "AMC Entertainment Holdings Inc.",
            "price": 2.50,
            "market_cap": 13000000000,
            "volume": 3000000,
            "bid_spread": 0.10,
            "sentiment_score": 0.60,  # Slightly bearish
            "news_score": 0.50,  # Bearish
            "unusual_score": 0.10  # Normal options activity
        },
        {
            "ticker": "BTC-USD",
            "name": "Bitcoin USD",
            "price": 35000.00,
            "market_cap": 650000000000,
            "volume": 25000000000,
            "bid_spread": 0.00,
            "sentiment_score": 0.75,  # Bullish (institutional adoption)
            "news_score": 0.80,  # Bullish (ETF approval)
            "unusual_score": 0.00  # No options data
        }
    ]
    
    logger.info(f"Loaded {len(mock_stock_data)} stock candidates")
    
    # Find best stocks
    min_score_threshold = RISK_CAPS["sentiment_threshold"]
    best_stocks = find_best_stocks(mock_stock_data, min_score=min_score_threshold)
    
    logger.info(f"Found {len(best_stocks)} high-conviction stocks")
    
    # Calculate optimal order size
    if len(best_stocks) > 0:
        optimal_size = bankroll / len(best_stocks)
    else:
        optimal_size = 0.0
    
    optimal_size = max(optimal_size, 0.01)
    optimal_size = min(optimal_size, max_pos_usd)
    
    logger.info(f"Optimal order size: ${optimal_size:.2f} per stock")
    
    # Generate orders (in shadow mode for now)
    orders = []
    total_volume = 0.0
    
    for stock in best_stocks:
        ticker = stock.get("ticker", "")
        order_side = "buy"  # Always buy for stock hunter (momentum trading)
        order_price = stock.get("price", 1.0)
        
        # Risk check
        if optimal_size < 0.01:
            logger.debug(f"Skipping {ticker}: size too small")
            continue
        
        # Create order
        order = {
            "ticker": ticker,
            "side": order_side,
            "size": optimal_size,
            "price": order_price,
            "sentiment_score": stock.get("sentiment_score", 0.0),
            "news_score": stock.get("news_score", 0.0),
            "unusual_score": stock.get("unusual_score", 0.0),
            "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        orders.append(order)
        total_volume += optimal_size
    
    # Summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Stocks analyzed: {len(mock_stock_data)}")
    logger.info(f"Best stocks: {len(best_stocks)}")
    logger.info(f"Total orders: {len(orders)}")
    logger.info(f"Total volume: ${total_volume:.2f}")
    
    if best_stocks:
        best_stock = max(best_stocks, key=lambda x: x.get("sentiment_score", 0) + x.get("news_score", 0) if isinstance(x, dict) else 0)
        logger.info(f"Best stock: {best_stock.get('ticker')} (combined score: {(best_stock.get('sentiment_score', 0) + best_stock.get('news_score', 0)):.2f})")
    
    logger.info("=" * 60)
    
    # Generate proof
    from runner import generate_proof
    proof_id = f"phase3_stock_hunter_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": mode,
        "bankroll": bankroll,
        "max_pos_usd": max_pos_usd,
        "data": {
            "orders": orders,
            "summary": {
                "total_stocks": len(mock_stock_data),
                "best_stocks": len(best_stocks),
                "total_orders": len(orders),
                "total_volume": total_volume,
                "best_stock": best_stocks[0].get("ticker") if best_stocks else None
            }
        },
        "risk_caps": RISK_CAPS
    }
    
    generate_proof(proof_id, proof_data)
    
    logger.info(f"Proof: {proof_id}")
    
    return len(orders)

def main():
    parser = argparse.ArgumentParser(description="Stock Hunter - Phase 3")
    parser.add_argument("--mode", choices=["shadow", "real-live"], default="shadow", help="Execution mode")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=100.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    result = optimize_stock_hunter_strategy(
        mode=args.mode,
        bankroll=args.bankroll,
        max_pos_usd=args.max_pos
    )
    
    logger.info("=" * 60)
    logger.info(f"Exit code: {result}")
    
    return result

if __name__ == "__main__":
    main()
