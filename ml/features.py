"""
Feature engineering for prediction market ML training data.
"""

import math
from datetime import datetime, timezone
from typing import Optional


def compute_price_deltas(
    current_price: float,
    price_1h_ago: Optional[float],
    price_6h_ago: Optional[float],
    price_24h_ago: Optional[float],
) -> dict:
    """
    Compute price change features.
    All deltas are in absolute price terms (not percentage).
    """
    return {
        "price_delta_1h": current_price - price_1h_ago if price_1h_ago is not None else None,
        "price_delta_6h": current_price - price_6h_ago if price_6h_ago is not None else None,
        "price_delta_24h": current_price - price_24h_ago if price_24h_ago is not None else None,
    }


def compute_volatility_24h(price_history: list[float]) -> Optional[float]:
    """
    Compute standard deviation of price changes over 24h window.
    Requires at least 2 price points to compute changes.
    """
    if len(price_history) < 2:
        return None
    changes = [price_history[i] - price_history[i - 1] for i in range(1, len(price_history))]
    if not changes:
        return None
    mean_change = sum(changes) / len(changes)
    variance = sum((c - mean_change) ** 2 for c in changes) / len(changes)
    return math.sqrt(variance)


def compute_momentum(
    price_delta_1h: Optional[float],
    price_delta_6h: Optional[float],
) -> Optional[float]:
    """
    Momentum: weighted combination of recent price changes.
    Positive = upward momentum, negative = downward.
    """
    if price_delta_1h is None and price_delta_6h is None:
        return None
    if price_delta_6h is None:
        return price_delta_1h
    if price_delta_1h is None:
        return price_delta_6h / 6  # normalize 6h to per-hour
    # 1h delta weighted 2x, 6h delta normalized to per-hour
    return (2 * price_delta_1h) + (price_delta_6h / 6)


def compute_mean_reversion_signal(
    current_price: float,
    price_24h_avg: Optional[float],
) -> Optional[float]:
    """
    Mean reversion signal: how far current price is from 24h average.
    Positive = above average (potential overbought),
    Negative = below average (potential oversold).
    """
    if price_24h_avg is None:
        return None
    return current_price - price_24h_avg


def compute_temporal_features(observed_at: datetime) -> dict:
    """Extract temporal features from observation timestamp."""
    utc = observed_at.astimezone(timezone.utc) if observed_at.tzinfo else observed_at
    return {
        "day_of_week": utc.weekday(),  # 0=Monday, 6=Sunday
        "hour_of_day": utc.hour,
        "is_weekend": utc.weekday() >= 5,
    }


def compute_category_encoding(category: str) -> dict:
    """
    Simple label encoding for market categories.
    Returns integer code.
    """
    # Common categories in Kalshi
    CATEGORY_CODES = {
        "economics": 0,
        "finance": 1,
        "politics": 2,
        "elections": 3,
        "companies": 4,
        "climate": 5,
        "weather": 6,
        "world": 7,
        "crypto": 8,
        "science": 9,
        "technology": 10,
        "sports": 11,
        "esports": 12,
        "entertainment": 13,
        "social": 14,
        "other": 15,
    }
    return {"category_encoded": CATEGORY_CODES.get(category.lower(), 15)}


def compute_pnl_labels(
    outcome: int,  # 1=YES, 0=NO
    yes_price: float,
    no_price: float,
) -> dict:
    """
    Compute theoretical PnL labels for YES and NO positions
    bought at the observation price.
    """
    return {
        "pnl_if_bought_yes": float(outcome) - yes_price,
        "pnl_if_bought_no": float(1 - outcome) - no_price,
    }
