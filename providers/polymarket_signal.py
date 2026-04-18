"""
Polymarket read-only signal provider.

Reads public Polymarket prices via gamma-api (no auth required).
Computes delta vs Kalshi prices for prompt enrichment.
ALL execution stays on Kalshi — this is signal only.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, any]] = {}
CACHE_TTL = 600
RATE_LIMIT_INTERVAL = 1.0
_last_request_time = 0.0
MAX_REQUESTS_PER_RUN = 10
_requests_this_run = 0

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

TICKER_SEARCH_MAP = {
    "KXINX": "S&P 500 close above",
    "KXINXU": "S&P 500 close above",
    "INX": "S&P 500 close above",
    "KXNDX": "NASDAQ",
    "KXNASDAQ100": "NASDAQ",
    "KXBTC": "price of Bitcoin above",
    "KXETH": "price of Ethereum",
    "KXETHY": "price of Ethereum",
    "KXMVESPORTS": None,
    "KXBUNDESLIGA": None,
    "KXCOACH": None,
    "KXGOV": None,
}


def reset_run_counter():
    global _requests_this_run
    _requests_this_run = 0


def _rate_limit():
    global _last_request_time, _requests_this_run
    if _requests_this_run >= MAX_REQUESTS_PER_RUN:
        raise RuntimeError(f"Polymarket rate limit: {MAX_REQUESTS_PER_RUN} requests/run exceeded")
    elapsed = time.time() - _last_request_time
    if elapsed < RATE_LIMIT_INTERVAL:
        time.sleep(RATE_LIMIT_INTERVAL - elapsed)
    _last_request_time = time.time()
    _requests_this_run += 1


def _get_cached(key: str) -> Optional[any]:
    if key in _cache:
        ts, result = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return result
        del _cache[key]
    return None


def _set_cache(key: str, result: any):
    _cache[key] = (time.time(), result)


def search_markets(query: str, limit: int = 200) -> list[dict]:
    cache_key = f"search:{query}:{limit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    _rate_limit()
    try:
        r = requests.get(
            f"{GAMMA_API_BASE}/markets",
            params={"limit": limit, "closed": "false", "order": "volume24hr", "ascending": "false"},
            timeout=15,
        )
        r.raise_for_status()
        all_markets = r.json()

        query_lower = query.lower()
        query_words = set(query_lower.split())
        results = []
        for m in all_markets:
            question = m.get("question", "")
            q_lower = question.lower()
            word_overlap = len(query_words & set(q_lower.split()))
            if word_overlap >= 1 or query_lower in q_lower or _similarity(query, question) > 0.35:
                results.append(m)

        _set_cache(cache_key, results)
        logger.info("[polymarket] search '%s': %d matches from %d markets", query, len(results), len(all_markets))
        return results
    except Exception as e:
        logger.warning("[polymarket] search failed for '%s': %s", query, e)
        return []


def get_market_price(market: dict) -> dict:
    try:
        outcome_prices = market.get("outcomePrices", "")
        if isinstance(outcome_prices, str):
            prices = json.loads(outcome_prices) if outcome_prices else []
        else:
            prices = outcome_prices

        yes_price = float(prices[0]) if len(prices) > 0 else None
        no_price = float(prices[1]) if len(prices) > 1 else None

        return {
            "question": market.get("question", ""),
            "yes_price": yes_price,
            "no_price": no_price,
            "volume": market.get("volume", 0),
            "liquidity": market.get("liquidity", 0),
            "url": f"https://polymarket.com/event/{market.get('slug', '')}",
            "end_date": market.get("endDate"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.warning("[polymarket] price parse failed: %s", e)
        return {}


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _extract_search_terms(kalshi_ticker: str, kalshi_title: str = "") -> Optional[str]:
    for prefix, terms in TICKER_SEARCH_MAP.items():
        if kalshi_ticker.upper().startswith(prefix):
            if terms is None:
                return None
            return terms

    if kalshi_title:
        title = re.sub(
            r"\b(Will|the|be|above|below|at or above|at or below|on|by|close|end)\b",
            "", kalshi_title, flags=re.I,
        )
        words = [w for w in title.split() if len(w) > 3 and not w.isdigit()]
        if words:
            return " ".join(words[:5])

    return None


def _extract_strike_from_ticker(ticker: str) -> Optional[str]:
    """Try to extract strike price from Kalshi ticker.

    E.g. KXBTC-26APR18-T80000 -> '80000', KXINXU-26APR20H1600-T7124.9999 -> '7124'
    """
    parts = ticker.split("-")
    for p in parts:
        if p.startswith("T") and len(p) > 1:
            strike = p[1:].replace(".9999", "").replace(".999", "")
            try:
                val = float(strike)
                if val >= 100:
                    return strike
            except ValueError:
                pass
        if p.startswith("B") and len(p) > 1:
            strike = p[1:]
            try:
                float(strike)
                return strike
            except ValueError:
                pass
    return None


def find_matching_market(
    kalshi_ticker: str,
    kalshi_title: str = "",
    kalshi_yes_price: Optional[float] = None,
) -> Optional[dict]:
    search_terms = _extract_search_terms(kalshi_ticker, kalshi_title)
    if not search_terms:
        return None

    cache_key = f"match:{kalshi_ticker}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    markets = search_markets(search_terms)
    if not markets:
        return None

    compare_text = kalshi_title or kalshi_ticker
    strike = _extract_strike_from_ticker(kalshi_ticker)
    best_match = None
    best_score = 0.0

    for m in markets:
        question = m.get("question", "")
        score = _similarity(compare_text, question)
        if strike and strike in question:
            score += 0.2
        if score > best_score and score >= 0.45:
            best_score = score
            best_match = m

    if not best_match:
        logger.debug("[polymarket] no match for %s (best=%.2f)", kalshi_ticker, best_score)
        return None

    price_data = get_market_price(best_match)
    if not price_data or price_data.get("yes_price") is None:
        return None

    result = {
        **price_data,
        "match_score": best_score,
        "kalshi_ticker": kalshi_ticker,
    }

    if kalshi_yes_price is not None and price_data["yes_price"] is not None:
        result["delta"] = price_data["yes_price"] - kalshi_yes_price
        result["delta_pct"] = (result["delta"] / kalshi_yes_price * 100) if kalshi_yes_price > 0 else 0
    else:
        result["delta"] = None
        result["delta_pct"] = None

    _set_cache(cache_key, result)
    logger.info(
        "[polymarket] match %s: '%s' (score=%.2f, poly_yes=%.3f)",
        kalshi_ticker, price_data["question"][:50], best_score, price_data["yes_price"],
    )
    return result


def format_poly_context(match: dict) -> str:
    if not match:
        return ""

    lines = ["## Cross-Venue Signal (Polymarket)"]
    lines.append(f"Matching market: \"{match.get('question', '?')}\"")
    yes_price = match.get("yes_price", 0)
    lines.append(f"Polymarket YES price: {yes_price:.3f}")

    if match.get("delta") is not None:
        direction = "higher" if match["delta"] > 0 else "lower"
        lines.append(
            f"Delta vs Kalshi: {match['delta']:+.3f} ({match['delta_pct']:+.1f}%) — "
            f"Polymarket is {direction}"
        )

    vol = match.get("volume", 0)
    if vol:
        lines.append(f"Polymarket volume: ${float(vol):,.0f}")

    lines.append(f"Match confidence: {match.get('match_score', 0):.0%}")
    return "\n".join(lines)


def enrich_market_text(text: str, kalshi_ticker: str, kalshi_title: str = "",
                       kalshi_yes_price: float = None) -> str:
    """Enrich market prompt text with Polymarket cross-venue signal."""
    try:
        match = find_matching_market(
            kalshi_ticker=kalshi_ticker,
            kalshi_title=kalshi_title,
            kalshi_yes_price=kalshi_yes_price,
        )
        if match:
            poly_section = format_poly_context(match)
            if poly_section:
                return text + "\n\n" + poly_section
    except Exception as e:
        logger.debug("[polymarket] enrichment skipped for %s: %s", kalshi_ticker, e)
    return text
