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
KALSHI_CANONICAL_BASE_URL = "https://api.elections.kalshi.com"


def _safe_float(value, default=0.0):
    """Convert API values to float safely."""
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _candidate_kalshi_base_urls():
    """Resolve configured Kalshi API URL with canonical fallback."""
    configured = (
        os.getenv("KALSHI_BASE_URL", KALSHI_CANONICAL_BASE_URL)
        .strip()
        .rstrip("/")
    )
    if not configured:
        configured = KALSHI_CANONICAL_BASE_URL

    if configured == KALSHI_CANONICAL_BASE_URL:
        return [configured]
    return [configured, KALSHI_CANONICAL_BASE_URL]


def _market_price_value(market, dollars_key, legacy_key):
    """
    Read current Kalshi *_dollars fields first, then legacy cent fields.
    Legacy fields may be cents (e.g. 37) or dollars (e.g. 0.37).
    """
    dollars_val = _safe_float(market.get(dollars_key, 0), 0.0)
    if dollars_val > 0:
        return dollars_val

    legacy_raw = market.get(legacy_key)
    if legacy_raw in (None, ""):
        return 0.0

    legacy_val = _safe_float(legacy_raw, 0.0)
    if legacy_val > 1.0:
        return legacy_val / 100.0
    return legacy_val


def get_kalshi_headers(method, path, api_key, private_key):
    """Generate Kalshi API headers"""
    timestamp = str(int(time.time() * 1000))  # milliseconds!
    path_without_query = path.split('?')[0]
    msg = f"{timestamp}{method}/trade-api/v2{path_without_query}"

    signature = private_key.sign(
        msg.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


def fetch_kalshi_series(api_key, private_key):
    """
    Fetch all series from Kalshi and filter by category
    
    Returns:
        List of series objects in target categories
    """
    try:
        headers = get_kalshi_headers('GET', '/series', api_key, private_key)
        resp = None
        selected_base = None
        for base_url in _candidate_kalshi_base_urls():
            candidate = requests.get(
                f"{base_url}/trade-api/v2/series",
                headers=headers,
                params={'include_volume': 'true'},
                timeout=15
            )
            if candidate.status_code == 200:
                resp = candidate
                selected_base = base_url
                break

            body_preview = candidate.text[:160].replace("\n", " ")
            logger.warning(
                "[KALSHI] Series API failed: status=%s base=%s body=%s",
                candidate.status_code,
                base_url,
                body_preview,
            )
            resp = candidate

        if resp is None or resp.status_code != 200:
            logger.error("Kalshi Series API error: no successful base URL")
            return []
        
        data = resp.json()
        all_series = data.get('series', [])
        
        logger.info(f"[KALSHI] Series discovery: {len(all_series)} total series (base={selected_base})")
        
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
        resp = None
        for base_url in _candidate_kalshi_base_urls():
            candidate = requests.get(
                f"{base_url}/trade-api/v2/markets",
                headers=headers,
                params={'series_ticker': series_ticker, 'status': 'open', 'limit': 100},
                timeout=15
            )
            if candidate.status_code == 200:
                resp = candidate
                break
            resp = candidate
        
        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else "n/a"
            logger.debug(f"No markets for series {series_ticker}: {status}")
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
        series_limit = int(os.getenv("KALSHI_SERIES_LIMIT", "50") or "50")
    except ValueError:
        series_limit = 50
    try:
        min_liquidity_usd = float(os.getenv("KALSHI_FETCH_MIN_LIQUIDITY_USD", "0") or "0")
    except ValueError:
        min_liquidity_usd = 0.0

    include_categories_raw = os.getenv("KALSHI_FETCH_INCLUDE_CATEGORIES", "").strip()
    include_categories = {
        c.strip().lower()
        for c in include_categories_raw.split(",")
        if c.strip()
    } if include_categories_raw else None

    try:
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        
        # Step 1: Discover target series by category
        target_series = fetch_kalshi_series(api_key, private_key)
        
        if not target_series:
            logger.warning("[KALSHI] No target series found - FAILING CLOSED (no fallback to sports)")
            return []
        
        # Step 2: Apply series limit and fetch markets from each selected series
        all_markets = []
        series_with_markets = 0

        # Sort by series volume (higher first), then lower fee multiplier.
        target_series.sort(key=lambda s: (
            -_safe_float(s.get('volume', 0), 0.0),
            _safe_float(s.get('fee_multiplier', 1), 1.0),
        ))

        selected_series = target_series if series_limit <= 0 else target_series[:series_limit]
        logger.info(
            f"[KALSHI FILTER] Series limit: {len(target_series)} -> {len(selected_series)} "
            f"(series_limit={series_limit})"
        )

        for series in selected_series:
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
        
        raw_count = len(all_markets)
        logger.info(f"[KALSHI FILTER] Raw API response count: {raw_count}")

        # Step 3: Status filter (API currently uses 'active', keep compatibility with 'open')
        status_filtered = []
        for m in all_markets:
            status = str(m.get('status', 'active') or 'active').lower()
            if status in {'active', 'open'}:
                status_filtered.append(m)
        logger.info(
            f"[KALSHI FILTER] After status filter: {len(status_filtered)} "
            f"(kept statuses: active/open)"
        )

        # Step 4: yes_ask > 0 filter and normalized price selection
        priced_markets = []
        ask_only_count = 0
        ask_from_last_trade_count = 0
        for m in status_filtered:
            yes_bid_price = _market_price_value(m, 'yes_bid_dollars', 'yes_bid')
            yes_ask_price = _market_price_value(m, 'yes_ask_dollars', 'yes_ask')
            if yes_ask_price <= 0:
                last_price = _market_price_value(m, 'last_price_dollars', 'last_price')
                if last_price <= 0:
                    continue
                yes_ask_price = last_price
                ask_from_last_trade_count += 1

            # Ask-only books are common; midpoint with bid=0 underprices by 50%.
            if yes_bid_price > 0:
                yes_price = (yes_bid_price + yes_ask_price) / 2.0
            else:
                yes_price = yes_ask_price
                ask_only_count += 1

            m['_yes_bid_price'] = yes_bid_price
            m['_yes_ask_price'] = yes_ask_price
            m['_yes_price'] = yes_price
            priced_markets.append(m)
        logger.info(
            f"[KALSHI FILTER] After yes_ask > 0 filter: {len(priced_markets)} "
            f"(ask-only books: {ask_only_count}, ask-from-last-trade: {ask_from_last_trade_count})"
        )

        # Step 5: Liquidity filter (default disabled; keep data flow open for strategy-level logic)
        liquidity_filtered = []
        for m in priced_markets:
            reported_liquidity_usd = _safe_float(m.get('liquidity_dollars', 0), 0.0)
            open_interest_units = _safe_float(
                m.get('open_interest_fp', m.get('open_interest', 0)),
                0.0,
            )
            implied_liquidity_usd = open_interest_units * m['_yes_price']
            liquidity_usd = reported_liquidity_usd if reported_liquidity_usd > 0 else implied_liquidity_usd
            m['_liquidity_usd'] = liquidity_usd

            volume_24h = _safe_float(m.get('volume_24h_fp', m.get('volume_24h', 0)), 0.0)
            if volume_24h <= 0:
                volume_24h = _safe_float(m.get('volume_fp', m.get('volume', 0)), 0.0)
            m['_volume_24h'] = volume_24h

            if min_liquidity_usd > 0 and liquidity_usd < min_liquidity_usd:
                continue
            liquidity_filtered.append(m)
        logger.info(
            f"[KALSHI FILTER] After liquidity filter: {len(liquidity_filtered)} "
            f"(min_liquidity_usd={min_liquidity_usd})"
        )

        # Step 6: Category filter (default disabled to include sports/crypto/high-liquidity markets)
        if include_categories is None:
            category_filtered = liquidity_filtered
            logger.info(
                f"[KALSHI FILTER] After category filter: {len(category_filtered)} "
                "(filter disabled; all categories included)"
            )
        else:
            category_filtered = []
            for m in liquidity_filtered:
                category = str(m.get('series_category', '')).strip().lower()
                if category in include_categories:
                    category_filtered.append(m)
            logger.info(
                f"[KALSHI FILTER] After category filter: {len(category_filtered)} "
                f"(include_categories={sorted(include_categories)})"
            )

        # Step 7: Final safety filter - reject blocked prefixes
        filtered_markets = []
        for m in category_filtered:
            ticker = m.get('ticker', '')
            if not any(ticker.upper().startswith(prefix) for prefix in KALSHI_BLOCKED_PREFIXES):
                filtered_markets.append(m)

        # Step 8: Sort by available liquidity/volume proxies (most active first)
        filtered_markets.sort(key=lambda m: -_safe_float(m.get('_volume_24h', 0), 0.0))

        # Log top 10
        top_10 = [
            (
                m.get('ticker'),
                _safe_float(m.get('_volume_24h', 0), 0.0),
                _safe_float(m.get('_yes_ask_price', 0), 0.0),
                _safe_float(m.get('_yes_bid_price', 0), 0.0),
            )
            for m in filtered_markets[:10]
        ]
        logger.info(f"[KALSHI] Top 10 by volume: {top_10}")

        # Step 9: Format markets for strategy use
        markets = []
        for m in filtered_markets:
            ticker = m.get('ticker', '')
            yes_bid_price = _safe_float(
                m.get('_yes_bid_price', _market_price_value(m, 'yes_bid_dollars', 'yes_bid')),
                0.0
            )
            yes_ask_price = _safe_float(
                m.get('_yes_ask_price', _market_price_value(m, 'yes_ask_dollars', 'yes_ask')),
                0.0
            )
            yes_price = _safe_float(m.get('_yes_price', yes_ask_price), 0.0)
            no_price = 1.0 - yes_price
            liquidity_usd = _safe_float(m.get('_liquidity_usd', 0), 0.0)
            volume_24h = _safe_float(m.get('_volume_24h', 0), 0.0)

            markets.append({
                "id": ticker,
                "question": m.get('short_name', m.get('title', ticker)),
                "odds": {"yes": yes_price, "no": no_price},
                "liquidity_usd": liquidity_usd,
                "volume_24h": volume_24h,
                "yes_bid_price": yes_bid_price,
                "yes_ask_price": yes_ask_price,
                "hours_to_end": 48,  # Placeholder
                "close_time": m.get('close_time'),
                "expiration_time": m.get('expiration_time'),
                "series_ticker": m.get('series_ticker'),
                "series_category": m.get('series_category'),
                "fee_multiplier": _safe_float(m.get('fee_multiplier', 1), 1.0),
                "fee_type": "quadratic" if _safe_float(m.get('fee_multiplier', 1), 1.0) < 1 else "standard",
            })

        if len(markets) == 0:
            logger.warning("[KALSHI] No markets with active trading prices. "
                          "This is normal outside market hours or if markets have closed. "
                          f"Found {len(all_markets)} markets from API but none have yes_ask_dollars > 0.")
        else:
            logger.info(f"[KALSHI FILTER] Final returned count: {len(markets)}")

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
                yes_bid_cents = float(m.get('yes_bid_dollars', 0) or 0)
                yes_ask_cents = float(m.get('yes_ask_dollars', 0) or 0)
                if yes_ask_cents <= 0:
                    continue
                yes_price = ((yes_bid_cents + yes_ask_cents) / 2)
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


def get_kalshi_balance():
    """
    Fetch account balance from Kalshi API.

    Returns:
        float: Balance in USD. Returns 0.0 on error (with WARNING logged).
    """
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv("KALSHI_SECRET_FILE", "./kalshi_private_key.pem")

    if not api_key:
        logger.warning("[WALLET] KALSHI_KEY not set, cannot fetch balance")
        return 0.0

    try:
        with open(secret_file, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    except Exception as e:
        logger.warning(f"[WALLET] Failed to load Kalshi private key: {e}")
        return 0.0

    path = "/trade-api/v2/portfolio/balance"
    timestamp = str(int(time.time() * 1000))
    msg = f"{timestamp}GET{path}"
    signature = private_key.sign(
        msg.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()
    headers = {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }

    try:
        resp = requests.get(
            f"https://api.elections.kalshi.com{path}",
            headers=headers,
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"[WALLET] Balance fetch request failed: {e}")
        return 0.0

    if resp.status_code != 200:
        body_preview = resp.text[:200].replace("\n", " ")
        logger.warning(
            f"[WALLET] Balance fetch failed: status={resp.status_code} body={body_preview}"
        )
        return 0.0

    try:
        data = resp.json()
        # Balance is returned in cents
        balance = data.get("balance", 0)
        # API returns balance in dollars (confirmed: returns 108 for $108 account)
        balance_usd = float(balance)
        logger.info(f"[WALLET] Fetched balance: ${balance_usd:.2f}")
        return balance_usd
    except Exception as e:
        logger.warning(f"[WALLET] Balance parse failed: {e} body={resp.text[:200]}")
        return 0.0


# Alias for backwards compatibility
get_kalshi_markets = fetch_kalshi_markets
