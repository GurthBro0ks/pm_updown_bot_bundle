# Macrocosmos SN13 Evaluation Report

**Evaluated:** Macrocosmos (Bittensor Subnet 13) as Twitter/X data source
**Date:** 2026-03-20
**Status:** SKIP — API key required, no usable free tier

---

## Summary

Macrocosmos SN13 is a Bittensor decentralised AI data marketplace. Their SDK (`macrocosmos` v3.1.0) provides gRPC-based access to Twitter/X and Reddit data via subnet 13 miners. **An API key is required — there is no usable free tier without signup.** Unable to execute live queries without an API key.

---

## Technical Findings

### SDK Installation
```
pip install macrocosmos --break-system-packages
# Successfully installed macrocosmos-3.1.0
```

### API Architecture
- **Protocol:** gRPC (not REST)
- **Base URL:** `constellation.api.cloud.macrocosmos.ai`
- **Client:** `macrocosmos.Sn13Client(api_key=...)` or `AsyncSn13Client`
- **Method:** `sn13.OnDemandData(source="x", keywords=[...], limit=N)`
- **Sources supported:** `"x"` (Twitter), `"reddit"`
- **Parameters:**
  - `source`: str — required
  - `keywords`: list[str] — max 5
  - `usernames`: list[str] — max 10
  - `start_date` / `end_date`: ISO 8601 strings
  - `limit`: int — default 100, max 1000
- **Response:** `{"status": "success"|"error", "data": [...], "meta": {...}}`
- **Data fields:** Dynamic protobuf `Struct` — no fixed schema; fields vary per source

### Authentication
- Requires `MACROCOSMOS_API_KEY` environment variable or explicit `api_key` parameter
- No free tier without signup confirmed
- Fake key → 500 UNKNOWN gRPC error (endpoint reachable but auth/validation fails)

### REST API Probe Results
| Endpoint | Status |
|---|---|
| `https://sn13.api.macrocosmos.ai/api/v1/on_demand_data_request` | 404 |
| `https://constellation.api.cloud.macrocosmos.ai/api/v1/on_demand_data_request` | 404 |

REST endpoints do not exist — all traffic is gRPC only.

---

## Data Quality Assessment

| Dimension | Score | Notes |
|---|---|---|
| **Data quality** | Unknown | Cannot test — requires API key |
| **Schema** | Unknown | Dynamic protobuf Struct — no fixed field names |
| **Freshness** | Unknown | No live data obtained |
| **Rate limits** | Unknown | Not documented |
| **API reliability** | N/A | Cannot test without key |

### Expected data fields (from protobuf analysis):
Based on SDK protobuf definitions, each item in `data` is a protobuf `Struct` (arbitrary key-value pairs). Expected fields for X source:
- `text` / `content`: tweet body
- `author` / `username`: account info
- `timestamp` / `created_at`: post time
- `likes` / `retweets` / `replies`: engagement metrics
- `url`: link to post

For Reddit:
- `title`, `body`, `author`, `created_utc`, `score`, `upvotes`, `subreddit`

### API responsiveness:
- gRPC channel opens successfully to `constellation.api.cloud.macrocosmos.ai`
- Fake key → 500 UNKNOWN (endpoint is live, auth/validation working)
- No response time data obtained

---

## Cost Comparison

### Macrocosmos
- No free tier advertised for SN13
- API key signup at https://app.macrocosmos.ai/account
- Bittensor subnet model — cost is TAO token based (decentralised mining)
- Cannot determine pricing without account

### Apify (our current approach)
- **Free tier:** 30 days, $5 free credits
- **X scraper (apify/x-scraper):** ~$0.01–0.05 per result page
- **Pro plan:** Starts at $49/month for 500 actor compute units
- Our current usage: MarketAux free tier exhausted; considering paid Apify

### Alternative free options:
| Service | Free tier | Twitter/X coverage |
|---|---|---|
| snscrape | Full fetch, no API key | Excellent, but rate limits apply |
| Nitter instances | RSS/JSON, no auth | Limited, many instances down |
| Reddit API (PRAW) | 60 requests/min | Good for subreddits |
| GDELT (current) | Free, rate-limited | No Twitter, only news/Web |

---

## SDK Code Example

```python
import macrocosmos
from macrocosmos.resources.sn13 import AsyncSn13
from macrocosmos.sn13_client import AsyncSn13Client
import asyncio

async def fetch_tweets(api_key: str, keywords: list[str], limit: int = 100):
    client = AsyncSn13Client(api_key=api_key, timeout=30)
    sn13 = AsyncSn13(client)
    result = await sn13.OnDemandData(
        source="x",
        keywords=keywords,
        limit=limit,
    )
    return result  # {"status": "success", "data": [...], "meta": {...}}
```

---

## Recommendation: SKIP

### Reasons:
1. **No free access** — API key required with no usable free tier. Cannot evaluate without signup + potential cost.
2. **gRPC-only** — No REST endpoint. More complex to integrate than Apify's REST API.
3. **Undocumented schema** — Data fields are dynamic protobuf Struct with no fixed schema. Unknown what fields are actually returned per post.
4. **Unknown pricing** — Bittensor TAO token model makes cost unpredictable. No clear USD pricing.
5. **Apify is already integrated** — Our current sentiment pipeline uses Apify (currently on free tier). Macrocosmos doesn't offer a clear advantage to justify migration effort.

### If we revisit later:
1. Sign up at https://app.macrocosmos.ai/account and get API key
2. Re-run `utils/test_macrocosmos.py --api-key $KEY --test`
3. Verify actual data schema with a real key before any integration work

### Better alternatives to evaluate instead:
1. **snscrape** — Full Twitter archive fetch, no API key needed, well-documented
2. **Reddit PRAW** — Free tier sufficient for subreddit sentiment tracking
3. **Direct Twitter API v2** — Official, but expensive ($100+/month for Basic tier)
4. **Third-party aggregators** (e.g., NewsAPI, ContextualWeb) — REST, predictable pricing

---

## Files Produced

- `utils/test_macrocosmos.py` — Evaluation script (evaluates API, prints summary table, saves raw JSON)
- `logs/macrocosmos-test-20260320_164458.json` — Unauthenticated probe results
- `docs/macrocosmos-evaluation.md` — This report
