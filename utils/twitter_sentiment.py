"""
Twitter/X sentiment via Apify Twitter Scraper.
Uses Apify's pre-built Twitter scraper actor to get recent tweets about a ticker.
Free tier: ~$5/month of compute should cover daily scans of top tickers.
"""
import os
import logging
from typing import Optional, Dict
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Only import if available — graceful degradation
try:
    from apify_client import ApifyClient
    APIFY_AVAILABLE = True
except ImportError:
    APIFY_AVAILABLE = False
    logger.warning("[TWITTER] apify-client not installed, Twitter sentiment disabled")


# Keyword sets for sentiment analysis
POSITIVE_WORDS = {
    "bullish", "moon", "buy", "long", "breakout", "surge", "rally",
    "upgrade", "beat", "strong", "growth", "profit", "gains", "up",
    "calls", "green", "pump", "soar", "rocket", "boom", "winner",
    "hold", "accumulate", "all-time", "high", "bull", "call", "calls"
}

NEGATIVE_WORDS = {
    "bearish", "crash", "sell", "short", "breakdown", "dump", "drop",
    "downgrade", "miss", "weak", "loss", "losses", "down", "puts",
    "red", "tank", "plunge", "fear", "overvalued", "bubble", "cut",
    "warning", "layoff", "bankrupt", "scam", "fail", "dead", "dead money"
}


def get_twitter_sentiment(ticker: str, max_tweets: int = 50) -> Optional[Dict]:
    """
    Scrape recent tweets mentioning $TICKER and analyze sentiment.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "TSLA")
        max_tweets: Maximum number of tweets to scrape (default 50)

    Returns:
        Dict with keys:
            - score: float (0.0-1.0, 0.5 = neutral)
            - tweet_count: int
            - positive: int
            - negative: int
            - neutral: int
        Returns None if unavailable or on error.
    """
    if not APIFY_AVAILABLE:
        return None

    api_token = os.getenv("APIFY_API_TOKEN")
    if not api_token:
        logger.warning("[TWITTER] APIFY_API_TOKEN not set in .env")
        return None

    client = ApifyClient(api_token)

    # Use Apify's Twitter Scraper actor
    try:
        run_input = {
            "searchTerms": [f"${ticker}"],  # Search for $AAPL, $TSLA, etc.
            "maxTweets": max_tweets,
            "sort": "Latest",
            "tweetLanguage": "en",
        }

        # Run the actor and wait for it to finish
        run = client.actor("apidojo/tweet-scraper").call(run_input=run_input, timeout_secs=120)

        # Get results
        tweets = []
        for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            tweets.append(item.get("full_text", item.get("text", "")))

        if not tweets:
            logger.info(f"[TWITTER] {ticker}: 0 tweets found")
            return None

        # Analyze sentiment based on keywords
        positive = 0
        negative = 0
        neutral = 0

        for tweet in tweets:
            tweet_lower = tweet.lower()
            pos_count = sum(1 for w in POSITIVE_WORDS if w in tweet_lower)
            neg_count = sum(1 for w in NEGATIVE_WORDS if w in tweet_lower)

            if pos_count > neg_count:
                positive += 1
            elif neg_count > pos_count:
                negative += 1
            else:
                neutral += 1

        total = positive + negative + neutral
        # Score: 0.0 = all negative, 0.5 = neutral, 1.0 = all positive
        score = (positive + neutral * 0.5) / total if total > 0 else 0.5

        result = {
            "score": round(score, 3),
            "tweet_count": total,
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
        }

        logger.info(f"[TWITTER] {ticker}: {total} tweets, +{positive}/-{negative}/~{neutral}, score={score:.3f}")
        return result

    except Exception as e:
        logger.error(f"[TWITTER] Error scraping {ticker}: {e}")
        return None
