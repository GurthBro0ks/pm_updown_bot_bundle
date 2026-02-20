# Stock Hunter API Requirements

Based on `strategies/stock_hunter.py`, the following APIs are needed:

## Required APIs

| API | Purpose | Cost | How to Get |
|-----|---------|------|------------|
| **Reddit API** | r/wallstreetbets, r/stocks sentiment | FREE | https://www.reddit.com/prefs/apps → Create App → Script → Get client_id & client_secret |
| **Finnhub** | News, stock data, sentiment | FREE tier: 60 calls/min | https://finnhub.io/register → Get API key |
| **Alpha Vantage** | Stock prices, news sentiment | FREE tier: 25 calls/day | https://www.alphavantage.co/support/#api-key → Get free key |
| **Polygon.io** | Options data, unusual activity | FREE tier: 5 calls/min | https://polygon.io/ → Sign up → Get API key |
| **X/Twitter API** | Social sentiment | $100/mo (Basic) | https://developer.twitter.com/ → Apply for Basic tier |

## Already Have

| API | Purpose | Status |
|-----|---------|--------|
| **Kalshi** | Prediction markets | ✅ KEY + SECRET in .env |
| **SEF** | Spot trading | ✅ Configured |

## Free Alternative to Twitter API

Instead of paying $100/mo for Twitter, consider:
- **Stocktwits API** (free) - https://api.stocktwits.com/development
- **Google Trends** (free) - https://trends.google.com/trends/api
- **Finviz scraper** (free, rate-limited)

## Implementation Priority

1. **Reddit API** (free, high signal for retail sentiment)
2. **Finnhub** (free, news + sentiment)
3. **Polygon.io** (free tier, options flow)
4. **Stocktwits** (free Twitter alternative)

## .env Additions Needed

```bash
# Reddit API
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=slimy-stock-hunter/1.0

# Finnhub
FINNHUB_API_KEY=your_finnhub_key

# Alpha Vantage (backup)
ALPHA_VANTAGE_API_KEY=your_av_key

# Polygon.io (options)
POLYGON_API_KEY=your_polygon_key

# Stocktwits (optional)
STOCKTWITS_CLIENT_ID=optional
STOCKTWITS_CLIENT_SECRET=optional
```

## Quick Start (Free Only)

```bash
# 1. Get Reddit credentials (5 min)
# Go to: https://www.reddit.com/prefs/apps
# Create app → Script → Fill name, redirect uri (http://localhost)
# Copy client_id (under app name) and client_secret

# 2. Get Finnhub key (2 min)
# Go to: https://finnhub.io/register
# Enter email → Get key instantly

# 3. Get Polygon key (2 min)
# Go to: https://polygon.io/
# Sign up → Dashboard → Copy API key
```
