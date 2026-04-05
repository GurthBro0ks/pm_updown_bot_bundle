"""Contract price signals and AI prior self-validation gate.

Provides momentum, zscore, volume confidence, expiry confidence, and
validate_prior() to filter dampened AI priors before Kelly sizing.
"""

from __future__ import annotations

import math
import statistics
from typing import List, Optional


def compute_momentum(prices: List[float], window: int = 5) -> Optional[float]:
    """
    Returns price change (current - past) over the rolling window.

    Args:
        prices: List of recent contract yes_price observations (oldest first).
        window: Number of periods to compare.

    Returns:
        Momentum as a float, or None if insufficient data.
    """
    if not prices or len(prices) < window:
        return None
    return prices[-1] - prices[-window]


def compute_zscore(prices: List[float], window: int = 10) -> Optional[float]:
    """
    Z-score of the current price vs its rolling mean and std.

    Args:
        prices: List of recent contract yes_price observations (oldest first).
        window: Number of periods for rolling stats.

    Returns:
        Z-score as a float, or None if insufficient data or near-zero std.
    """
    if not prices or len(prices) < window:
        return None
    window_prices = prices[-window:]
    mean = statistics.mean(window_prices)
    stdev = statistics.stdev(window_prices)
    if stdev < 1e-9:
        return None
    current = prices[-1]
    return (current - mean) / stdev


def volume_confidence(volume: int, median_volume: int = 500) -> float:
    """
    Scale signal confidence by contract volume using a sigmoid-ish curve.

    Args:
        volume: Current contract volume.
        median_volume: Reference median volume (default 500).

    Returns:
        Confidence in [0.3, 1.0].
    """
    ratio = volume / max(median_volume, 1)
    return 0.3 + 0.7 * ratio / (1.0 + ratio)


def expiry_confidence(hours_to_expiry: float) -> float:
    """
    Contracts closer to expiry with edge yield higher conviction.

    Args:
        hours_to_expiry: Hours remaining until contract expiry.

    Returns:
        Confidence score: 1.0 (<1h), 0.9 (<24h), 0.7 (<72h), else 0.5.
    """
    if hours_to_expiry < 1:
        return 1.0
    elif hours_to_expiry < 24:
        return 0.9
    elif hours_to_expiry < 72:
        return 0.7
    else:
        return 0.5


def validate_prior(
    prior: float,
    momentum: Optional[float] = None,
    zscore: Optional[float] = None,
    contract_price: Optional[float] = None,
) -> dict:
    """
    Validate and dampen an AI prior before Kelly sizing.

    Rules:
        a) Degenerate prior (<0.01 or >0.99): clamp to [0.05, 0.95], fail.
        b) Bullish prior but negative momentum (or vice versa): flag, ×0.7 confidence.
        c) Extreme z-score (|z| > 2.0): flag, ×0.8 confidence.
        d) Prior diverges from market by >0.30: dampen 70/30 toward market, flag.
        e) If final confidence < 0.4: fail validation.

    Args:
        prior: Raw AI prior probability in [0, 1].
        momentum: Price momentum (optional).
        zscore: Z-score of current price vs rolling mean (optional).
        contract_price: Current market contract yes_price (optional).

    Returns:
        dict with keys: passed (bool), adjusted_prior (float),
        reason (str), confidence (float 0-1), flags (list[str]).
    """
    flags: List[str] = []
    confidence = 1.0
    adjusted_prior = prior
    reasons: List[str] = []

    # Rule (a): degenerate prior
    if prior < 0.01 or prior > 0.99:
        if prior < 0.01:
            adjusted_prior = 0.05
        else:
            adjusted_prior = 0.95
        flags.append("degenerate_prior")
        confidence *= 0.5
        reasons.append(f"degenerate clamp {prior:.4f} → {adjusted_prior:.2f}")

    # Rule (b): momentum direction conflict
    if momentum is not None:
        is_bullish_prior = adjusted_prior > 0.5
        is_positive_momentum = momentum > 0
        if is_bullish_prior != is_positive_momentum:
            flags.append("momentum_conflict")
            confidence *= 0.7
            reasons.append(f"momentum {momentum:+.4f} conflicts with prior {adjusted_prior:.3f}")

    # Rule (c): extreme z-score
    if zscore is not None and abs(zscore) > 2.0:
        flags.append("extreme_zscore")
        confidence *= 0.8
        reasons.append(f"z-score {zscore:+.2f} extreme")

    # Rule (d): prior diverges from market
    if contract_price is not None:
        divergence = abs(adjusted_prior - contract_price)
        if divergence > 0.30:
            # Dampen: 70% AI prior, 30% market price
            dampened = 0.7 * adjusted_prior + 0.3 * contract_price
            flags.append("market_divergence")
            reasons.append(
                f"prior {adjusted_prior:.3f} diverges from market {contract_price:.3f} "
                f"by {divergence:.3f}, dampened → {dampened:.3f}"
            )
            adjusted_prior = dampened

    # Rule (e): confidence threshold
    passed = confidence >= 0.4

    if not passed:
        reasons.append(f"confidence {confidence:.3f} below 0.4 threshold")

    reason = "; ".join(reasons) if reasons else "ok"

    return {
        "passed": passed,
        "adjusted_prior": adjusted_prior,
        "reason": reason,
        "confidence": confidence,
        "flags": flags,
    }
