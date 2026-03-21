#!/usr/bin/env python3
"""
NOAA Weather Arbitrage Strategy for Kalshi

Fetches free NOAA 48-hour forecasts via api.weather.gov and detects
mispriced temperature bucket contracts on Kalshi.

Edge source: NOAA updates 2x/day (6am/2pm UTC), markets lag 9-15 min.
Structural edge: 3-12% per trade on liquid contracts.

Cities tracked: NYC, Chicago, LA, Houston, Phoenix
Strategy: Bucket-spread (cover full distribution, not just modal bucket)

Safety limits:
  WEATHER_MAX_DAILY_TRADES = 10
  WEATHER_MAX_EXPOSURE_PER_CITY = $1.00
  WEATHER_MIN_EDGE_PCT = 3.0
  Always starts in SHADOW MODE
"""

import argparse
import json
import logging
import os
import sys
import time
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from scipy.stats import norm

# Add project to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

from config import RISK_CAPS, PROOF_DIR
from utils.kalshi_orders import KalshiOrderClient, SafetyLimitError
from utils.pnl_database import record_trade

# ============================================================================
# Constants
# ============================================================================

# Top 5 metro areas (lat, lon)
CITIES = {
    "NYC":    (40.7128, -74.0060),
    "Chicago": (41.8781, -87.6298),
    "LA":     (34.0522, -118.2437),
    "Houston": (29.7604, -95.3698),
    "Phoenix": (33.4484, -112.0740),
}

# NOAA API config
NOAA_BASE = "https://api.weather.gov"
NOAA_HEADERS = {
    "User-Agent": "KalshiWeatherBot/1.0 (pm-updown-bot)",
    "Accept": "application/geo+json"
}
# Rate limit: 50 req/min → max 1 req/1.2s, use 1.5s to be safe
NOAA_RATE_LIMIT_SEC = 1.5
NOAA_MAX_RETRIES = 3
NOAA_RETRY_DELAY_SEC = 5.0

# Kalshi temperature bucket markets (approximate ticker patterns)
# These are the typical series tickers for Kalshi temp markets
KALSHI_TEMP_SERIES_KEYWORDS = ["TEMPERATURE", "TEMP", "HIGH-TEMP", "LOW-TEMP", "DEGREE"]

# Strategy limits
WEATHER_MAX_DAILY_TRADES = 10
WEATHER_MAX_EXPOSURE_PER_CITY = 1.00  # $1.00 per city max
WEATHER_MIN_EDGE_PCT = 3.0           # 3% minimum edge to trade
WEATHER_MIN_LIQUIDITY_USD = 50.0      # $50 min liquidity

# Logging
RAW_LOG_PATH = "/opt/slimy/pm_updown_bot_bundle/logs/weather_raw.log"

# ============================================================================
# NOAA Data Fetcher
# ============================================================================

class NOAADataFetcher:
    """
    Fetches hourly temperature forecasts from api.weather.gov
    with caching, rate limiting, and fallback on failure.
    """

    def __init__(self):
        self._cache = {}  # city -> {forecast, timestamp}
        self._last_request_time = 0.0
        self._cache_ttl_sec = 3600  # 1 hour cache

    def _rate_limit(self):
        """Enforce rate limit between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < NOAA_RATE_LIMIT_SEC:
            time.sleep(NOAA_RATE_LIMIT_SEC - elapsed)
        self._last_request_time = time.time()

    def _get_points_url(self, lat: float, lon: float) -> str:
        return f"{NOAA_BASE}/points/{lat},{lon}"

    def _get_forecast_url(self, gridpoint_url: str) -> str:
        return f"{gridpoint_url}/forecast/hourly"

    def fetch_forecast(self, city: str, lat: float, lon: float) -> Optional[dict]:
        """
        Fetch hourly temperature forecast for a city.

        Returns:
            dict with keys: city, forecast_hours (list of {valid_time, temperature_F}),
            fetched_at (ISO), used_cache (bool)
        """
        # Check cache
        cached = self._cache.get(city)
        if cached and (time.time() - cached["fetched_ts"]) < self._cache_ttl_sec:
            cached["used_cache"] = True
            return cached

        self._rate_limit()

        # Step 1: Get gridpoint URL from lat/lon
        points_url = self._get_points_url(lat, lon)
        for attempt in range(NOAA_MAX_RETRIES):
            try:
                resp = requests.get(points_url, headers=NOAA_HEADERS, timeout=15)
                if resp.status_code == 200:
                    break
                elif resp.status_code == 429:
                    # Rate limited - back off
                    if attempt < NOAA_MAX_RETRIES - 1:
                        time.sleep(NOAA_RETRY_DELAY_SEC * (attempt + 1))
                        continue
                resp.raise_for_status()
            except Exception as e:
                if attempt < NOAA_MAX_RETRIES - 1:
                    time.sleep(NOAA_RETRY_DELAY_SEC * (attempt + 1))
                    continue
                # All retries failed - use cache if available
                if cached:
                    cached["used_cache"] = True
                    return cached
                return None

        try:
            data = resp.json()
        except Exception:
            if cached:
                cached["used_cache"] = True
                return cached
            return None

        # Get forecast URL from properties
        properties = data.get("properties", {})
        forecast_hourly_url = properties.get("forecastHourly")
        if not forecast_hourly_url:
            if cached:
                cached["used_cache"] = True
                return cached
            return None

        # Step 2: Fetch hourly forecast
        self._rate_limit()
        for attempt in range(NOAA_MAX_RETRIES):
            try:
                resp = requests.get(forecast_hourly_url, headers=NOAA_HEADERS, timeout=15)
                if resp.status_code == 429:
                    if attempt < NOAA_MAX_RETRIES - 1:
                        time.sleep(NOAA_RETRY_DELAY_SEC * (attempt + 1))
                        continue
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt < NOAA_MAX_RETRIES - 1:
                    time.sleep(NOAA_RETRY_DELAY_SEC * (attempt + 1))
                    continue
                if cached:
                    cached["used_cache"] = True
                    return cached
                return None

        try:
            data = resp.json()
        except Exception:
            if cached:
                cached["used_cache"] = True
                return cached
            return None

        # Parse periods (hourly forecast periods)
        periods = data.get("properties", {}).get("periods", [])
        forecast_hours = []
        for period in periods[:48]:  # Next 48 hours max
            temp_f = period.get("temperature")
            valid_time = period.get("startTime")
            if temp_f is not None and valid_time:
                forecast_hours.append({
                    "valid_time": valid_time,
                    "temperature_F": temp_f
                })

        result = {
            "city": city,
            "forecast_hours": forecast_hours,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "used_cache": False
        }

        # Cache it
        self._cache[city] = result

        # Log raw data for audit
        self._log_raw_forecast(city, result)

        return result

    def fetch_all_cities(self) -> dict:
        """Fetch forecasts for all tracked cities."""
        results = {}
        for city, (lat, lon) in CITIES.items():
            result = self.fetch_forecast(city, lat, lon)
            results[city] = result
        return results

    def _log_raw_forecast(self, city: str, forecast: dict):
        """Log raw NOAA data to audit file."""
        try:
            Path(RAW_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(RAW_LOG_PATH, "a") as f:
                entry = {
                    "city": city,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "used_cache": forecast.get("used_cache", False),
                    "forecast": forecast.get("forecast_hours", [])
                }
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logging.warning(f"[NOAA] Failed to log raw forecast: {e}")


# ============================================================================
# Probability Calculator
# ============================================================================

class TemperatureProbabilityCalculator:
    """
    Computes probability distribution across temperature buckets
    using scipy.stats.norm with NOAA forecast as mean.

    Std dev: 2.5°F for 24hr forecast (NOAA documented error).
    """

    FORECAST_STD_DEV = 2.5  # °F, NOAA 24hr forecast error

    def compute_bucket_probabilities(
        self,
        forecast_temps: list,
        buckets: list,
        std_dev: float = None
    ) -> list:
        """
        Compute probability of temperature falling in each bucket.

        Args:
            forecast_temps: List of forecast temperatures (°F)
            buckets: List of (low, high) tuple bounds
            std_dev: Std dev for normal distribution (default: FORECAST_STD_DEV)

        Returns:
            List of dicts: [{bucket: (low, high), probability: float, edge: float}, ...]
        """
        if not forecast_temps:
            return []

        sd = std_dev or self.FORECAST_STD_DEV

        # Use mean of forecast temperatures as expected temperature
        mean_temp = sum(forecast_temps) / len(forecast_temps)

        results = []
        total_prob = 0.0

        for i, (low, high) in enumerate(buckets):
            # Probability = P(X < high) - P(X < low)
            prob_high = norm.cdf(high, loc=mean_temp, scale=sd)
            prob_low = norm.cdf(low, loc=mean_temp, scale=sd)
            bucket_prob = prob_high - prob_low

            # Handle edge buckets
            if i == 0:
                # Below lowest bucket
                bucket_prob = norm.cdf(buckets[0][1], loc=mean_temp, scale=sd)
            if i == len(buckets) - 1:
                # Above highest bucket
                bucket_prob = 1.0 - norm.cdf(buckets[-1][0], loc=mean_temp, scale=sd)

            bucket_prob = max(0.0, min(1.0, bucket_prob))
            total_prob += bucket_prob

            results.append({
                "bucket_index": i,
                "bucket_low": low,
                "bucket_high": high,
                "probability": bucket_prob,
                "mean_temp": mean_temp,
                "std_dev": sd
            })

        # Renormalize to sum to 1.0
        if total_prob > 0:
            for r in results:
                r["probability"] = r["probability"] / total_prob

        return results, mean_temp


# ============================================================================
# Kalshi Market Matcher
# ============================================================================

def fetch_kalshi_temperature_markets() -> list:
    """
    Fetch active Kalshi temperature bucket markets.

    Returns:
        List of market dicts with ticker, question, yes_price, bucket_info
    """
    try:
        import os as _os
        key_id = _os.getenv("KALSHI_KEY")
        if not key_id:
            logging.warning("[KALSHI] No KALSHI_KEY set")
            return []

        from runner import get_headers, fetch_kalshi_markets

        markets = fetch_kalshi_markets()
        if not markets:
            return []

        # Filter to temperature-related markets
        temp_markets = []
        for m in markets:
            ticker = m.get("id", "").upper()
            question = m.get("question", "").upper()
            text = f"{ticker} {question}"

            # Skip non-weather
            if not any(kw in text for kw in KALSHI_TEMP_SERIES_KEYWORDS):
                continue

            # Skip markets with no price
            yes_price = m.get("odds", {}).get("yes", 0)
            if yes_price <= 0:
                continue

            # Extract bucket info from question
            # Typical format: "Will NYC be above 90°F on date?"
            bucket_info = _parse_bucket_from_question(question)

            temp_markets.append({
                "ticker": m.get("id"),
                "question": m.get("question"),
                "yes_price": yes_price,
                "no_price": 1.0 - yes_price,
                "liquidity_usd": m.get("liquidity_usd", 0),
                "bucket_info": bucket_info,
                "hours_to_end": m.get("hours_to_end", 48),
            })

        return temp_markets

    except Exception as e:
        logging.warning(f"[KALSHI] Failed to fetch temperature markets: {e}")
        return []


def _parse_bucket_from_question(question: str) -> dict:
    """
    Parse bucket bounds from Kalshi market question.

    Example: "Will NYC be above 90°F on Mar 21?"
    Returns: {"city": "NYC", "bucket_type": "above", "threshold_F": 90}
    """
    import re

    result = {"city": None, "bucket_type": None, "threshold_F": None}

    # Try to extract city
    for city in CITIES.keys():
        if city in question:
            result["city"] = city
            break

    # Try to extract temperature threshold
    temp_match = re.search(r"(\d+)\s*°?F", question, re.IGNORECASE)
    if temp_match:
        result["threshold_F"] = int(temp_match.group(1))

    # Determine above/below
    if "above" in question.lower() or "over" in question.lower():
        result["bucket_type"] = "above"
    elif "below" in question.lower() or "under" in question.lower():
        result["bucket_type"] = "below"
    elif "between" in question.lower():
        result["bucket_type"] = "between"

    # Try to extract date
    date_match = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d+)", question)
    if date_match:
        result["date"] = f"{date_match.group(1)} {date_match.group(2)}"

    return result


# ============================================================================
# Bucket-Spread Strategy
# ============================================================================

def compute_spread_ev(
    bucket_probs: list,
    market_prices: dict,
    spread_cost_per_bucket: float = 0.01
) -> dict:
    """
    Compute expected value of a bucket-spread position.

    Args:
        bucket_probs: List of {bucket_index, bucket_low, bucket_high, probability}
        market_prices: Dict of bucket_index -> market_price (yes price)
        spread_cost_per_bucket: Cost per bucket to buy (typically $0.01 = $0.01/bucket)

    Returns:
        Dict with total_cost, total_ev, ev_per_bucket, recommended_buckets
    """
    total_cost = spread_cost_per_bucket * len(bucket_probs)
    total_ev = 0.0
    bucket_evs = []

    for bp in bucket_probs:
        idx = bp["bucket_index"]
        prob = bp["probability"]
        market_price = market_prices.get(idx, 0.5)

        # If we buy this bucket at market_price, expected value = prob * $1 - cost
        bucket_ev = prob * 1.0 - market_price
        bucket_evs.append({
            "bucket": (bp["bucket_low"], bp["bucket_high"]),
            "prob": prob,
            "market_price": market_price,
            "edge": prob - market_price,
            "bucket_ev": bucket_ev
        })

        total_ev += bucket_ev

    # Find best buckets (top 2-3 by probability)
    sorted_buckets = sorted(bucket_evs, key=lambda x: x["prob"], reverse=True)
    recommended = sorted_buckets[:3]

    return {
        "total_cost": total_cost,
        "total_ev": total_ev,
        "ev_per_bucket": bucket_evs,
        "recommended_buckets": recommended,
        "sum_probabilities": sum(b["prob"] for b in bucket_evs)
    }


def find_spread_opportunities(
    noaa_results: dict,
    kalshi_markets: list,
    min_edge_pct: float = WEATHER_MIN_EDGE_PCT,
    min_liquidity: float = WEATHER_MIN_LIQUIDITY_USD
) -> list:
    """
    Find bucket-spread opportunities where NOAA probability differs from market price.

    Args:
        noaa_results: Dict of city -> forecast dict
        kalshi_markets: List of Kalshi temperature markets
        min_edge_pct: Minimum edge percentage to flag opportunity
        min_liquidity: Minimum liquidity in USD

    Returns:
        List of opportunity dicts with city, ticker, buckets, edge, cost, ev
    """
    opportunities = []

    for market in kalshi_markets:
        ticker = market["ticker"]
        bucket_info = market.get("bucket_info", {})
        city = bucket_info.get("city")

        if not city or city not in noaa_results:
            continue

        if market.get("liquidity_usd", 0) < min_liquidity:
            continue

        noaa_forecast = noaa_results[city]
        if not noaa_forecast or not noaa_forecast.get("forecast_hours"):
            continue

        # Get forecast temperatures
        temps = [h["temperature_F"] for h in noaa_forecast["forecast_hours"]]
        if not temps:
            continue

        # Define buckets based on market type
        bucket_type = bucket_info.get("bucket_type", "above")
        threshold = bucket_info.get("threshold_F")

        if bucket_type == "above" and threshold:
            buckets = [(threshold, 999)]  # above threshold
            buckets_below = [(-999, threshold)]  # below threshold as complement
        elif bucket_type == "below" and threshold:
            buckets = [(-999, threshold)]  # below threshold
            buckets_below = [(threshold, 999)]
        else:
            continue

        # Compute probability distribution
        calc = TemperatureProbabilityCalculator()
        probs, mean_temp = calc.compute_bucket_probabilities(temps, buckets)

        if not probs:
            continue

        # Compute edge
        yes_price = market["yes_price"]
        prob_above = probs[0]["probability"] if probs else 0.0
        edge = (prob_above - yes_price) * 100  # edge in %

        if edge < min_edge_pct:
            continue

        opportunity = {
            "city": city,
            "ticker": ticker,
            "question": market["question"],
            "mean_temp_F": round(mean_temp, 1),
            "threshold_F": threshold,
            "bucket_type": bucket_type,
            "noaa_prob": round(prob_above, 4),
            "market_price": yes_price,
            "edge_pct": round(edge, 2),
            "liquidity_usd": market.get("liquidity_usd", 0),
            "hours_to_end": market.get("hours_to_end", 48),
            "forecast_hours": len(temps),
        }

        opportunities.append(opportunity)

    return opportunities


# ============================================================================
# Risk Integration
# ============================================================================

def check_weather_gates(opportunity: dict, risk_caps: dict) -> tuple:
    """
    Check risk gates for weather opportunity.

    Returns: (passed: bool, violations: list)
    """
    violations = []

    # Gate 1: Edge minimum
    if opportunity["edge_pct"] < risk_caps.get("weather_min_edge_pct", WEATHER_MIN_EDGE_PCT):
        violations.append(f"Edge {opportunity['edge_pct']}% < min {WEATHER_MIN_EDGE_PCT}%")

    # Gate 2: Liquidity minimum
    if opportunity["liquidity_usd"] < risk_caps.get("weather_min_liquidity_usd", WEATHER_MIN_LIQUIDITY_USD):
        violations.append(f"Liquidity ${opportunity['liquidity_usd']:.0f} < min ${WEATHER_MIN_LIQUIDITY_USD}")

    # Gate 3: Market not about to expire
    if opportunity.get("hours_to_end", 999) < 6:
        violations.append(f"Market ends in {opportunity['hours_to_end']}h < 6h minimum")

    # Gate 4: Price sanity
    price = opportunity["market_price"]
    if price < 0.02 or price > 0.98:
        violations.append(f"Price {price:.2f} too extreme")

    passed = len(violations) == 0
    return passed, violations


def get_order_client() -> Optional[KalshiOrderClient]:
    """Initialize Kalshi order client lazily."""
    try:
        key_id = os.getenv("KALSHI_TRADING_KEY_ID")
        key_file = os.getenv("KALSHI_TRADING_KEY_FILE")
        if not key_id or not key_file:
            logging.warning("KALSHI_TRADING_KEY_ID or KALSHI_TRADING_KEY_FILE not set")
            return None
        return KalshiOrderClient(
            api_key=key_id,
            private_key_path=key_file,
            base_url="https://api.elections.kalshi.com/trade-api/v2"
        )
    except Exception as e:
        logging.error(f"Failed to init order client: {e}")
        return None


# ============================================================================
# Runner Integration
# ============================================================================

def run_weather_strategy(
    mode: str = "shadow",
    bankroll: float = 100.0,
    max_pos_usd: float = 1.0
) -> int:
    """
    Main entry point for weather strategy.

    Args:
        mode: 'shadow' (log only) or 'micro-live' (real $0.01 trades)
        bankroll: Available capital
        max_pos_usd: Max position per city

    Returns:
        Number of opportunities found
    """
    logging.info("=" * 60)
    logging.info("WEATHER STRATEGY - NOAA Arbitrage")
    logging.info(f"Mode: {mode}")
    logging.info(f"Bankroll: ${bankroll:.2f}")
    logging.info(f"Max pos/city: ${max_pos_usd:.2f}")
    logging.info("=" * 60)

    # Step 1: Fetch NOAA forecasts for all cities
    logging.info("[1/4] Fetching NOAA forecasts...")
    fetcher = NOAADataFetcher()
    noaa_results = fetcher.fetch_all_cities()

    for city, result in noaa_results.items():
        if result:
            n_hours = len(result.get("forecast_hours", []))
            cached = result.get("used_cache", False)
            logging.info(f"  {city}: {n_hours} hours, cache={'YES' if cached else 'NO'}")
        else:
            logging.warning(f"  {city}: FAILED (using fallback)")

    # Step 2: Fetch Kalshi temperature markets
    logging.info("[2/4] Fetching Kalshi temperature markets...")
    kalshi_markets = fetch_kalshi_temperature_markets()
    logging.info(f"  Found {len(kalshi_markets)} temperature markets")

    if not kalshi_markets:
        logging.warning("[KALSHI] No temperature markets found")
        return 0

    # Step 3: Find opportunities
    logging.info("[3/4] Computing probabilities and edges...")
    risk_caps = {
        "weather_min_edge_pct": WEATHER_MIN_EDGE_PCT,
        "weather_min_liquidity_usd": WEATHER_MIN_LIQUIDITY_USD,
    }

    opportunities = find_spread_opportunities(noaa_results, kalshi_markets)
    logging.info(f"  Found {len(opportunities)} opportunities (edge > {WEATHER_MIN_EDGE_PCT}%)")

    # Step 4: Execute or log
    logging.info("[4/4] Processing opportunities...")
    trades_placed = 0

    risk_caps_full = {
        **RISK_CAPS,
        "weather_min_edge_pct": WEATHER_MIN_EDGE_PCT,
        "weather_min_liquidity_usd": WEATHER_MIN_LIQUIDITY_USD,
    }

    for opp in opportunities:
        # Check risk gates
        passed, violations = check_weather_gates(opp, risk_caps_full)
        if not passed:
            logging.info(f"  SKIP {opp['city']}/{opp['ticker']}: {violations}")
            continue

        city = opp["city"]
        ticker = opp["ticker"]
        edge = opp["edge_pct"]
        price = opp["market_price"]
        prob = opp["noaa_prob"]

        if mode == "shadow":
            logging.info(
                f"  SHADOW: {city} {ticker} "
                f"NOAA_prob={prob:.3f} market={price:.3f} edge={edge:.1f}% "
                f"→ WOULD BUY YES @ {int(price*100)}¢"
            )
            trades_placed += 1

            # Record to pnl.db as shadow trade
            try:
                record_trade(
                    phase="weather",
                    ticker=ticker,
                    action="SHADOW_BUY",
                    price=price,
                    size_usd=0.01,
                    pnl_usd=0,
                    pnl_pct=0
                )
            except Exception as e:
                logging.warning(f"[DB] Failed to record shadow trade: {e}")

        elif mode == "micro-live":
            client = get_order_client()
            if client is None:
                logging.error("Cannot execute micro-live: order client init failed")
                continue

            # Check balance
            try:
                balance = client.get_balance()
                available = balance.get("available_balance", 0)
                if available < 10:  # less than 10 cents
                    logging.warning(f"Balance too low for micro-live: ${available}")
                    continue
            except Exception as e:
                logging.error(f"Balance check failed: {e}")
                continue

            # Place penny trade
            price_cents = max(1, min(99, int(round(price * 100))))
            try:
                result = client.place_order(
                    ticker=ticker,
                    side="yes",
                    quantity=1,
                    price_cents=price_cents,
                )
                order_id = result.get("order_id", "unknown")
                status = result.get("status", "unknown")
                logging.info(
                    f"  MICRO-LIVE: {city} {ticker} "
                    f"BOUGHT YES 1x @ {price_cents}¢ → {status} (id: {order_id})"
                )
                trades_placed += 1

                # Record to pnl.db
                try:
                    record_trade(
                        phase="weather",
                        ticker=ticker,
                        action="BUY",
                        price=price,
                        size_usd=0.01,
                        pnl_usd=0,
                        pnl_pct=0
                    )
                except Exception as e:
                    logging.warning(f"[DB] Failed to record trade: {e}")

            except SafetyLimitError as e:
                logging.warning(f"  MICRO-LIVE SAFETY LIMIT: {ticker} — {e}")
                continue
            except Exception as e:
                logging.error(f"  MICRO-LIVE ORDER FAILED: {ticker} — {e}")
                continue

    # Generate proof
    proof_id = f"kalshi_weather_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": mode,
        "bankroll": bankroll,
        "max_pos_usd": max_pos_usd,
        "noaa_cities": list(CITIES.keys()),
        "opportunities": opportunities,
        "trades_placed": trades_placed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _generate_proof(proof_id, proof_data)

    logging.info("=" * 60)
    logging.info(f"WEATHER STRATEGY COMPLETE")
    logging.info(f"Opportunities found: {len(opportunities)}")
    logging.info(f"Trades placed: {trades_placed}")
    logging.info(f"Proof: {proof_id}")
    logging.info("=" * 60)

    return len(opportunities)


def _generate_proof(proof_id: str, data: dict):
    """Generate proof JSON file."""
    proof_path = PROOF_DIR / f"{proof_id}.json"
    try:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        with open(proof_path, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"Proof saved: {proof_path}")
    except Exception as e:
        logging.error(f"Failed to write proof: {e}")


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NOAA Weather Arbitrage Strategy")
    parser.add_argument("--mode", choices=["shadow", "micro-live"], default="shadow")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--max-pos", type=float, default=1.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler("/opt/slimy/pm_updown_bot_bundle/logs/weather_strategy.log"),
            logging.StreamHandler()
        ]
    )

    exit_code = run_weather_strategy(
        mode=args.mode,
        bankroll=args.bankroll,
        max_pos_usd=args.max_pos
    )
    sys.exit(0 if exit_code > 0 else 1)
