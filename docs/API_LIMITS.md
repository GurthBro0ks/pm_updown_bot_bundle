# API Rate Limits & Usage Plan

## Current API Limits

| API | Limit | Period | Notes |
|-----|-------|--------|-------|
| **Alpha Vantage** | 25 requests | DAY | ⚠️ Very limited - use as backup only |
| **Massive (Polygon)** | 5 requests | MIN | Free tier - causes 429 errors if exceeded |
| **Finnhub** | 60 requests | MIN | Free tier |
| **Marketaux** | 100 requests | MONTH | Free tier - limited |
| **Mediastack** | 500 requests | MONTH | Free tier |
| **WorldNewsAPI** | 100 requests | MONTH | Free tier |
| **CoinGecko** | 10-50 requests | MIN | Free tier |
| **Binance.US** | 1200 requests | MIN | Read-only key |
| **WeatherAPI.com** | 1M requests | MONTH | Free tier |
| **Open-Meteo** | 10,000 requests | DAY | FREE (no key) |
| **PredictIt** | Unlimited | - | FREE (no key) |
| **Kalshi** | ~100 requests | MIN | With API key |
| **Stocktwits** | Unlimited | - | Public API |

## Problem APIs

### Alpha Vantage (25/day)
- **Status**: ⚠️ EXHAUSTED
- **Fix**: Only use as fallback when Massive fails

### Massive/Polygon (5/min)
- **Status**: ⚠️ RATE LIMITED
- **Fix**: Add 12-second delays between calls (5/min = 1 per 12s)

### Marketaux (100/month)
- **Status**: ⚠️ LOW LIMIT
- **Fix**: Use sparingly, cache results

## Bot API Usage Plan

### Per Cycle (15 min intervals)
- 10 tickers analyzed
- Max API calls per cycle:
  - Massive: 10 calls (need 12s delay each = 2 min total) ⚠️
  - Finnhub: 10 calls (OK - 60/min)
  - Marketaux: 10 calls (⚠️ would exhaust in 10 cycles)

### Recommended Approach

1. **Price Data**: Use Massive with 12s delays (or Alpha Vantage as backup)
2. **Sentiment**: Prioritize Finnhub (higher limit), use Marketaux sparingly
3. **News**: Finnhub first, Marketaux/WorldNewsAPI as backup
4. **Caching**: Cache results for 15 min to avoid redundant calls

### Rate Limit Safe Configuration

```python
# Target tickers reduced from 10 to 5
TARGET_TICKERS = ["AAPL", "TSLA", "NVDA", "GME", "META"]

# API call delays
MASSIVE_DELAY_S = 12  # 5 calls/min max
MARKETAUX_CACHE_MIN = 15  # Cache for 15 min
```

## Alternative: Use Finnhub for Everything

Finnhub supports:
- Company news ✅
- Stock prices (basic) ✅
- Sentiment (via news) ✅

**Recommendation**: Switch to Finnhub as primary, use others as backup.

## Implementation

1. Add rate limiting to each API call
2. Implement caching for price/sentiment data
3. Reduce ticker list to 5 high-priority stocks
4. Add fallback chain: Massive → Alpha Vantage → cached

---

*Updated: 2026-02-20*
