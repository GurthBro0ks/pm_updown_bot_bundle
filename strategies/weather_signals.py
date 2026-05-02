"""
Weather Signal Generator — GFS Ensemble for Kalshi KXHIGH Markets

Core strategy module. Uses 31-member GFS ensemble forecasts to generate
trade signals for Kalshi daily high temperature markets.

Strategy:
1. Discover open KXHIGH markets via data/weather_markets.py
2. Fetch GFS ensemble forecast for each market's city via data/weather_forecast.py
3. Calculate model probability: P(high > threshold) = count(members > threshold) / 31
4. Compute edge = model_probability - market_price
5. Apply filters and Kelly sizing
6. Return trade signals

Filters:
- |edge| >= 8% (minimum edge for weather — higher than AI markets)
- ensemble_confidence: at least 20/31 members must agree (>65% consensus)
- volume > 0 (market has some activity)
- spread (yes_ask - yes_bid) <= 15 cents

Kelly Sizing:
- kelly = (win_prob * odds - lose_prob) / odds
- position = kelly * 0.15 * bankroll (15% fractional Kelly)
- Cap at min(5% of bankroll, $1.00) per trade
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone

from data.weather_forecast import (
    get_ensemble_forecast_for_city_code,
    get_ensemble_bin_probability,
)
from data.weather_markets import discover_weather_markets

logger = logging.getLogger(__name__)

# Strategy parameters
MIN_EDGE_PCT = 0.08          # 8% minimum edge
MIN_ENSEMBLE_CONFIDENCE = 0.65  # At least 20/31 members must agree
MAX_SPREAD_CENTS = 15.0      # Max spread in cents
MIN_VOLUME = 0               # Minimum volume (0 = any activity)
FRACTIONAL_KELLY = 0.15      # 15% fractional Kelly
MAX_POSITION_PCT = 0.05      # Max 5% of bankroll per trade
MAX_POSITION_USD = 1.00      # Hard cap at $1.00 per trade
DEFAULT_BANKROLL = 100.0     # Default bankroll for sizing


def compute_kelly_size(
    win_prob: float,
    market_price: float,
    bankroll: float = DEFAULT_BANKROLL,
) -> float:
    """
    Compute Kelly-optimal position size.

    Args:
        win_prob: Probability of winning (0.0 to 1.0)
        market_price: Market price (cost per contract, 0.0 to 1.0)
        bankroll: Available bankroll in USD

    Returns:
        Position size in USD
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0

    # Odds: if we win, we get $1.00 per contract. We paid market_price.
    # Net profit per contract = 1.0 - market_price
    # Net loss per contract = market_price
    # Kelly fraction = (win_prob * odds - lose_prob) / odds
    # But in prediction markets, "odds" = (1 - market_price) / market_price
    # Standard Kelly for binary: f = (bp - q) / b
    #   where b = odds received = (1 - price) / price
    #         p = win probability
    #         q = 1 - p

    b = (1.0 - market_price) / market_price  # odds ratio
    p = win_prob
    q = 1.0 - p

    kelly_fraction = (b * p - q) / b

    # Apply fractional Kelly
    position_fraction = kelly_fraction * FRACTIONAL_KELLY

    # Convert to USD
    position_usd = position_fraction * bankroll

    # Apply caps
    max_by_bankroll = bankroll * MAX_POSITION_PCT
    position_usd = min(position_usd, max_by_bankroll, MAX_POSITION_USD)
    position_usd = max(position_usd, 0.0)  # No negative positions

    return position_usd


def generate_weather_signals(
    bankroll: float = DEFAULT_BANKROLL,
    dry_run: bool = False,
    max_markets: int = 50,
) -> List[Dict]:
    """
    Generate weather trading signals from GFS ensemble forecasts.

    Args:
        bankroll: Available bankroll for position sizing
        dry_run: If True, log but don't actually place orders
        max_markets: Maximum number of markets to analyze

    Returns:
        List of signal dicts with:
            ticker, city, city_code, threshold, ensemble_prob, market_price,
            edge, edge_pct, confidence, volume, spread_cents, position_usd,
            side, kelly_fraction, hours_to_close
    """
    signals = []

    # Step 1: Discover weather markets
    logger.info("[WEATHER_SIGNALS] Discovering KXHIGH markets...")
    markets = discover_weather_markets(max_hours_to_close=48.0)

    if not markets:
        logger.info("[WEATHER_SIGNALS] No weather markets found")
        return signals

    logger.info(f"[WEATHER_SIGNALS] Analyzing {min(len(markets), max_markets)} markets...")

    # Step 2: Analyze each market
    for market in markets[:max_markets]:
        ticker = market["ticker"]
        city_code = market["city_code"]
        threshold = market["threshold"]
        market_price = market["mid_price"]
        volume = market["volume"]
        spread_cents = market["spread_cents"]
        hours_to_close = market["hours_to_close"]

        # Filter: spread too wide
        if spread_cents > MAX_SPREAD_CENTS:
            logger.debug(f"[WEATHER_SIGNALS] SKIP {ticker}: spread={spread_cents:.1f}c > {MAX_SPREAD_CENTS}c")
            continue

        # Filter: no volume
        if volume < MIN_VOLUME:
            logger.debug(f"[WEATHER_SIGNALS] SKIP {ticker}: volume={volume} < {MIN_VOLUME}")
            continue

        # Step 3: Fetch ensemble forecast
        daily_highs = get_ensemble_forecast_for_city_code(city_code)
        if not daily_highs or len(daily_highs) < 20:
            logger.warning(f"[WEATHER_SIGNALS] SKIP {ticker}: insufficient ensemble data ({len(daily_highs)} members)")
            continue

        # Step 4: Calculate ensemble probability
        # KXHIGH markets are temperature BINS (e.g., "64-65°F")
        # For B64.5, the bin is [64, 65] (1°F width)
        bin_low = threshold - 0.5
        bin_high = threshold + 0.5
        ensemble_prob = get_ensemble_bin_probability(daily_highs, bin_low, bin_high)

        # Step 5: Calculate edge
        edge = ensemble_prob - market_price
        edge_pct = abs(edge)

        # Filter: minimum edge
        if edge_pct < MIN_EDGE_PCT:
            logger.debug(
                f"[WEATHER_SIGNALS] SKIP {ticker}: edge={edge_pct:.1%} < {MIN_EDGE_PCT:.1%}"
            )
            continue

        # Step 6: Ensemble confidence
        # Confidence = fraction of members that agree with the majority direction
        members_in_bin = sum(1 for t in daily_highs if bin_low <= t <= bin_high)
        members_out_bin = len(daily_highs) - members_in_bin
        confidence = max(members_in_bin, members_out_bin) / len(daily_highs)

        # Filter: minimum confidence
        if confidence < MIN_ENSEMBLE_CONFIDENCE:
            logger.debug(
                f"[WEATHER_SIGNALS] SKIP {ticker}: confidence={confidence:.1%} < {MIN_ENSEMBLE_CONFIDENCE:.1%}"
            )
            continue

        # Step 7: Determine side
        # If ensemble_prob > market_price, buy YES (model thinks it's more likely than market)
        # If ensemble_prob < market_price, buy NO (model thinks it's less likely than market)
        if ensemble_prob > market_price:
            side = "yes"
            win_prob = ensemble_prob
        else:
            side = "no"
            win_prob = 1.0 - ensemble_prob

        # Step 8: Kelly sizing
        position_usd = compute_kelly_size(win_prob, market_price, bankroll)

        # Filter: position too small
        if position_usd < 0.01:
            logger.debug(f"[WEATHER_SIGNALS] SKIP {ticker}: position=${position_usd:.2f} < $0.01")
            continue

        # Step 9: Build signal
        signal = {
            "ticker": ticker,
            "city": market["city_name"],
            "city_code": city_code,
            "threshold": threshold,
            "bin_low": bin_low,
            "bin_high": bin_high,
            "ensemble_prob": ensemble_prob,
            "market_price": market_price,
            "edge": edge,
            "edge_pct": edge_pct,
            "confidence": confidence,
            "members_in_bin": members_in_bin,
            "members_out_bin": members_out_bin,
            "total_members": len(daily_highs),
            "volume": volume,
            "spread_cents": spread_cents,
            "position_usd": position_usd,
            "side": side,
            "kelly_fraction": position_usd / bankroll if bankroll > 0 else 0,
            "hours_to_close": hours_to_close,
            "date_str": market["date_str"],
            "comparison": market["comparison"],
            "yes_bid": market["yes_bid"],
            "yes_ask": market["yes_ask"],
        }

        signals.append(signal)

        logger.info(
            f"[WEATHER_SIGNALS] SIGNAL: {ticker} | {city_code} [{bin_low}-{bin_high}]°F | "
            f"ensemble={ensemble_prob:.1%} market={market_price:.1%} edge={edge:+.1%} | "
            f"confidence={confidence:.1%} ({members_in_bin}/{len(daily_highs)} in bin) | "
            f"side={side.upper()} size=${position_usd:.2f}"
        )

    logger.info(f"[WEATHER_SIGNALS] Generated {len(signals)} signals from {len(markets)} markets")
    return signals


def get_signal_summary(signals: List[Dict]) -> str:
    """Generate a human-readable summary of signals."""
    if not signals:
        return "No weather signals generated"

    lines = [f"Weather Signals: {len(signals)} found"]
    lines.append("-" * 80)

    for s in signals:
        lines.append(
            f"  {s['ticker']}: {s['city']} [{s['bin_low']}-{s['bin_high']}]°F | "
            f"ensemble={s['ensemble_prob']:.1%} market={s['market_price']:.1%} "
            f"edge={s['edge']:+.1%} | {s['side'].upper()} ${s['position_usd']:.2f}"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    signals = generate_weather_signals(dry_run=True)
    print(get_signal_summary(signals))
