"""
Kalshi API utilities - Fixed Series-Based Market Discovery
Uses Series API to filter out sports/esports and target financial markets
"""

import os
import requests
import time
import logging
from datetime import datetime
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
import base64

logger = logging.getLogger(__name__)

# REMOVED 2026-03-14: KALSHI_TARGET_CATEGORIES - legacy allowlist, now using blocklist-only filtering
# Previously defined categories: Economics, Politics, Financials, Elections, Companies,
# Climate and Weather, World, Crypto, Science and Technology

# Explicit blocklist — never trade these
KALSHI_BLOCKED_CATEGORIES = {
    "Entertainment",    # 2138 series - mostly celebrity/TV
    "Mentions",        # 271 series - social media mentions
    "Social",          # 75 series - social events
    "Exotics",         # 8 series - weird exotic markets
    # REMOVED 2026-03-14: Sports, Esports - needed for current active markets
}

# REMOVED 2026-03-14: All prefixes removed to allow esports/sports markets
# Previously blocked: KXMV, KXMVESPORTS, KXNFL, KXNBA, KXMLB, KXNHL, KXEPL, KXUCL
KALSHI_BLOCKED_PREFIXES = ()


def get_kalshi_headers(method, path, api_key, private_key):
    """Generate Kalshi API headers"""
    timestamp = str(int(time.time()))
    msg = f"{timestamp}{method}/trade-api/v2{path}"
    
    signature = private_key.sign(
        msg.encode(),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    sig_b64 = base64.b64encode(signature).decode()
    
    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp
    }


def fetch_kalshi_series(api_key, private_key):
    """
    Fetch all series from Kalshi and filter by category
    
    Returns:
        List of series objects in target categories
    """
    try:
        headers = get_kalshi_headers('GET', '/series', api_key, private_key)
        resp = requests.get(
            'https://api.elections.kalshi.com/trade-api/v2/series',
            headers=headers,
            params={'include_volume': 'true'},
            timeout=15
        )
        
        if resp.status_code != 200:
            logger.error(f"Kalshi Series API error: {resp.status_code}")
            return []
        
        data = resp.json()
        all_series = data.get('series', [])
        
        logger.info(f"[KALSHI] Series discovery: {len(all_series)} total series")
        
        # Filter series by category
        target_series = []
        blocked_count = 0
        
        for s in all_series:
            category = s.get('category', '')
            ticker = s.get('ticker', '')
            
            # Check if blocked by prefix first
            if any(ticker.upper().startswith(prefix) for prefix in KALSHI_BLOCKED_PREFIXES):
                blocked_count += 1
                continue
            
            # Check category - blocklist only (2026-03-14)
            if category in KALSHI_BLOCKED_CATEGORIES:
                blocked_count += 1
                continue

            # All non-blocked categories now pass through
            target_series.append(s)
        
        logger.info(f"[KALSHI] Series filter: {len(target_series)} target, {blocked_count} blocked")
        
        # Log target series tickers
        target_tickers = [s.get('ticker') for s in target_series[:20]]
        logger.info(f"[KALSHI] Target series: {target_tickers}")
        
        return target_series
        
    except Exception as e:
        logger.error(f"Kalshi Series API error: {e}")
        return []


def fetch_markets_for_series(series_ticker, api_key, private_key):
    """
    Fetch open markets for a specific series
    
    Args:
        series_ticker: Series ticker (e.g., "KXINXSP5" for S&P 500 range)
        api_key: Kalshi API key
        private_key: RSA private key for signing
    
    Returns:
        List of market objects
    """
    try:
        headers = get_kalshi_headers('GET', '/markets', api_key, private_key)
        resp = requests.get(
            'https://api.elections.kalshi.com/trade-api/v2/markets',
            headers=headers,
            params={'series_ticker': series_ticker, 'status': 'open', 'limit': 50},
            timeout=15
        )
        
        if resp.status_code != 200:
            logger.debug(f"No markets for series {series_ticker}: {resp.status_code}")
            return []
        
        data = resp.json()
        return data.get('markets', [])
        
    except Exception as e:
        logger.debug(f"Error fetching markets for {series_ticker}: {e}")
        return []


def fetch_kalshi_markets():
    """
    Fetch open markets from Kalshi API - FIXED VERSION
    
    Now uses Series API to discover financial/economic/political markets
    instead of fetching ALL markets (which were 100% esports)
    """
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
    
    if not api_key:
        logger.warning("KALSHI_KEY not set")
        return []
    
    try:
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        
        # Step 1: Discover target series by category
        target_series = fetch_kalshi_series(api_key, private_key)
        
        if not target_series:
            logger.warning("[KALSHI] No target series found - FAILING CLOSED (no fallback to sports)")
            return []
        
        # Step 2: Fetch markets from each target series
        all_markets = []
        series_with_markets = 0
        
        # Sort by fee_multiplier (lower is better) and volume
        target_series.sort(key=lambda s: (
            s.get('fee_multiplier', 0.07),  # Lower fees first
            -s.get('volume', 0) or 0         # Higher volume first
        ))
        
        for series in target_series[:20]:  # Limit to top 20 series to avoid rate limits
            series_ticker = series.get('ticker')
            markets = fetch_markets_for_series(series_ticker, api_key, private_key)
            
            if markets:
                series_with_markets += 1
                
                # Add series metadata to each market
                for m in markets:
                    m['series_ticker'] = series_ticker
                    m['series_category'] = series.get('category')
                    m['fee_multiplier'] = series.get('fee_multiplier', 0.07)
                    m['series_volume'] = series.get('volume', 0)
                
                all_markets.extend(markets)
        
        logger.info(f"[KALSHI] Open markets found: {len(all_markets)} across {series_with_markets} series")
        
        # Step 3: Final safety filter - reject blocked prefixes
        filtered_markets = []
        for m in all_markets:
            ticker = m.get('ticker', '')
            if not any(ticker.upper().startswith(prefix) for prefix in KALSHI_BLOCKED_PREFIXES):
                filtered_markets.append(m)
        
        # Step 4: Sort by volume_24h (most liquid first)
        filtered_markets.sort(key=lambda m: -(m.get('volume_24h', 0) or 0))
        
        # Log top 10
        top_10 = [(m.get('ticker'), m.get('volume_24h', 0) or 0) for m in filtered_markets[:10]]
        logger.info(f"[KALSHI] Top 10 by volume: {top_10}")
        
        # Step 5: Format markets for strategy use
        markets = []
        for m in filtered_markets:
            ticker = m.get('ticker', '')
            yes_bid_cents = m.get('yes_bid', 0)
            yes_ask_cents = m.get('yes_ask', 0)
            
            if yes_ask_cents <= 0:
                continue
            
            yes_price = ((yes_bid_cents + yes_ask_cents) / 2) / 100.0
            no_price = 1.0 - yes_price
            liquidity_usd = (m.get('open_interest', 0) or 0) * yes_price
            
            markets.append({
                "id": ticker,
                "question": m.get('short_name', m.get('title', ticker)),
                "odds": {"yes": yes_price, "no": no_price},
                "liquidity_usd": liquidity_usd,
                "volume_24h": m.get('volume_24h', 0) or 0,
                "hours_to_end": 48,  # Placeholder
                "series_ticker": m.get('series_ticker'),
                "series_category": m.get('series_category'),
                "fee_multiplier": m.get('fee_multiplier', 0.07),
                "fee_type": "quadratic" if m.get('fee_multiplier', 0.07) < 0.05 else "standard"
            })

        if len(markets) == 0:
            logger.warning("[KALSHI] No markets with active trading prices. "
                          "This is normal outside market hours or if markets have closed. "
                          f"Found {len(all_markets)} markets from API but none have yes_ask_cents > 0.")
        else:
            logger.info(f"[KALSHI] Returning {len(markets)} filtered markets")

        return markets
        
    except Exception as e:
        logger.error(f"Kalshi API error: {e}")
        return []


# Legacy function for backwards compatibility
def fetch_kalshi_markets_legacy():
    """
    LEGACY - Fetches all markets without category filtering
    This was returning 100% esports markets
    """
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
    
    if not api_key:
        return []
    
    try:
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        
        headers = get_kalshi_headers('GET', '/markets', api_key, private_key)
        resp = requests.get(
            'https://api.elections.kalshi.com/trade-api/v2/markets',
            headers=headers,
            params={'status': 'open', 'limit': 100},
            timeout=10
        )
        
        if resp.status_code == 200:
            data = resp.json() if resp.text.strip() else {"markets": []}
            markets = []
            for m in data.get('markets', []):
                ticker = m.get('ticker', '')
                yes_bid_cents = m.get('yes_bid', 0)
                yes_ask_cents = m.get('yes_ask', 0)
                if yes_ask_cents <= 0:
                    continue
                yes_price = ((yes_bid_cents + yes_ask_cents) / 2) / 100.0
                no_price = 1.0 - yes_price
                liquidity_usd = m.get('open_interest', 0) * yes_price
                markets.append({
                    "id": ticker,
                    "question": m.get('short_name', ticker),
                    "odds": {"yes": yes_price, "no": no_price},
                    "liquidity_usd": liquidity_usd,
                    "hours_to_end": 48
                })
            return markets
    except Exception as e:
        print(f"Kalshi API error: {e}")

    return []


def kalshi_debug_discovery():
    """
    One-time diagnostic: dump what the API actually returns.

    Use this to discover what categories the API returns so we can
    update KALSHI_TARGET_CATEGORIES accordingly.

    Run with:
        python3 -c "from utils.kalshi import kalshi_debug_discovery; kalshi_debug_discovery()"
    """
    import json
    logger.info("[KALSHI DEBUG] Starting discovery...")

    # Get ALL series
    url = "https://api.elections.kalshi.com/trade-api/v2/series"
    resp = requests.get(url, params={"include_volume": True}, timeout=15)
    series_data = resp.json()

    categories = {}
    for s in series_data.get("series", []):
        cat = s.get("category", "UNKNOWN")
        categories.setdefault(cat, []).append({
            "ticker": s["ticker"],
            "title": s.get("title", ""),
            "volume": s.get("volume", 0),
        })

    # Write diagnostic output
    with open("/opt/slimy/pm_updown_bot_bundle/kalshi_debug.json", "w") as f:
        json.dump({
            "total_series": len(series_data.get("series", [])),
            "categories": {k: len(v) for k, v in categories.items()},
            "category_details": categories,
        }, f, indent=2)

    logger.info(f"[KALSHI DEBUG] Categories found: {list(categories.keys())}")

    # Get first 100 open markets (no filter)
    url2 = "https://api.elections.kalshi.com/trade-api/v2/markets"
    resp2 = requests.get(url2, params={"status": "open", "limit": 100}, timeout=15)
    markets = resp2.json().get("markets", [])

    ticker_prefixes = {}
    for m in markets:
        prefix = m["ticker"][:6] if len(m["ticker"]) >= 6 else m["ticker"]
        ticker_prefixes.setdefault(prefix, []).append(m["ticker"])

    with open("/opt/slimy/pm_updown_bot_bundle/kalshi_markets_debug.json", "w") as f:
        json.dump({
            "total_open_markets": len(markets),
            "ticker_prefix_counts": {k: len(v) for k, v in ticker_prefixes.items()},
            "sample_markets": [{"ticker": m["ticker"], "title": m.get("title", ""), "volume_24h": m.get("volume_24h", 0)} for m in markets[:20]],
        }, f, indent=2)

    logger.info(f"[KALSHI DEBUG] Total open markets: {len(markets)}")
    logger.info(f"[KALSHI DEBUG] Top ticker prefixes: {dict(list(ticker_prefixes.items())[:10])}")
    logger.info(f"[KALSHI DEBUG] Output written to kalshi_debug.json and kalshi_markets_debug.json")

    return categories


# Alias for backwards compatibility
get_kalshi_markets = fetch_kalshi_markets
