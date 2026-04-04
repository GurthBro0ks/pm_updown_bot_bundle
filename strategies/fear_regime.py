#!/usr/bin/env python3
"""
Fear and Greed Index + VIX Regime Detection Layer

Fetches CNN Fear & Greed Index (via alternative.me API) and VIX (via yfinance),
computes market regime, and returns min_edge override for trading decisions.
"""

import logging
import time
from typing import Optional

import requests
import yfinance

logger = logging.getLogger(__name__)

# Module-level caches: {(value, classification, timestamp, fetch_time)}
_fg_cache: Optional[dict] = None
_vix_cache: Optional[tuple[float, float]] = None  # (vix_value, fetch_time)

CACHE_TTL_SECONDS = 3600  # 1 hour


def fetch_fear_greed() -> Optional[dict]:
    """
    Fetch CNN Fear and Greed Index via alternative.me API.

    Returns:
        dict with keys: value (int 0-100), classification (str), timestamp (str)
        None on failure.
    """
    global _fg_cache

    # Return cached if still valid
    if _fg_cache is not None:
        value, classification, ts, fetch_time = _fg_cache
        if time.time() - fetch_time < CACHE_TTL_SECONDS:
            logger.debug(f"[fear_regime] FG cache hit: value={value} classification={classification}")
            return {"value": value, "classification": classification, "timestamp": ts}

    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if resp.status_code != 200:
            logger.warning(f"[fear_regime] Fear/Greed API returned {resp.status_code}")
            return None

        data = resp.json()
        items = data.get("data", [])
        if not items:
            logger.warning("[fear_regime] Fear/Greed API returned empty data")
            return None

        item = items[0]
        value = int(item.get("value", -1))
        classification = str(item.get("value_classification", "Unknown"))
        ts = str(item.get("timestamp", ""))

        if value < 0:
            logger.warning(f"[fear_regime] Invalid FG value: {value}")
            return None

        _fg_cache = (value, classification, ts, time.time())
        logger.info(f"[fear_regime] Fetched FG: value={value} classification={classification}")
        return {"value": value, "classification": classification, "timestamp": ts}

    except Exception as e:
        logger.warning(f"[fear_regime] Fear/Greed fetch failed: {e}")
        return None


def fetch_vix() -> Optional[float]:
    """
    Fetch latest VIX close price via yfinance.

    Returns:
        float VIX close price, or None on failure.
    """
    global _vix_cache

    # Return cached if still valid
    if _vix_cache is not None:
        vix_value, fetch_time = _vix_cache
        if time.time() - fetch_time < CACHE_TTL_SECONDS:
            logger.debug(f"[fear_regime] VIX cache hit: {vix_value}")
            return vix_value

    try:
        # Download last 5 days of VIX to ensure we get at least one close
        hist = yfinance.download("^VIX", period="5d", progress=False, timeout=10)
        if hist.empty:
            logger.warning("[fear_regime] VIX download returned empty DataFrame")
            return None

        # Handle both flat and MultiIndex column DataFrames
        close_col = hist["Close"]
        if hasattr(close_col, "columns"):
            close_col = close_col.iloc[:, 0]
        vix_close = float(close_col.iloc[-1])
        if vix_close <= 0:
            logger.warning(f"[fear_regime] Invalid VIX close: {vix_close}")
            return None

        _vix_cache = (vix_close, time.time())
        logger.info(f"[fear_regime] Fetched VIX: {vix_close:.2f}")
        return vix_close

    except Exception as e:
        logger.warning(f"[fear_regime] VIX fetch failed: {e}")
        return None


def get_regime() -> dict:
    """
    Compute market regime from Fear & Greed Index and VIX.

    Returns:
        dict with keys:
            fear_greed_value (int or None),
            fear_greed_class (str or None),
            vix (float or None),
            regime (str): "extreme_fear" | "extreme_greed" | "high_vol" | "neutral",
            min_edge_override (float): edge threshold for this regime
    """
    fg_data = fetch_fear_greed()
    vix = fetch_vix()

    fg_value = fg_data["value"] if fg_data else None
    fg_class = fg_data["classification"] if fg_data else None

    # Determine regime
    regime = "neutral"
    min_edge = 0.15  # default stricter threshold

    if fg_value is not None:
        if fg_value < 20:
            regime = "extreme_fear"
            min_edge = 0.10
        elif fg_value > 80:
            regime = "extreme_greed"
            min_edge = 0.10

    if vix is not None and vix > 30:
        regime = "high_vol"
        min_edge = 0.12

    result = {
        "fear_greed_value": fg_value,
        "fear_greed_class": fg_class,
        "vix": vix,
        "regime": regime,
        "min_edge_override": min_edge,
    }
    logger.info(
        f"[fear_regime] Regime: Fear={fg_value} VIX={vix} -> {regime} (min_edge={min_edge})"
    )
    return result
