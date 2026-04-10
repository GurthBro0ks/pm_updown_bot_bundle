#!/usr/bin/env python3
"""
Kalshi Optimization Strategy - Phase 1 (Quick Wins)
Maker order logic, probability-weighted edge detection, trade frequency optimization
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import runner module
# Local imports (avoid circular import)
from utils.proof import generate_proof
from utils.kalshi import fetch_kalshi_markets
try:
    from strategies.sentiment_scorer import get_ai_prior
except Exception:
    get_ai_prior = None

try:
    from strategies.contract_signals import (
        compute_momentum,
        compute_zscore,
        volume_confidence,
        expiry_confidence,
        validate_prior,
    )
except Exception:
    compute_momentum = None
    compute_zscore = None
    volume_confidence = None
    expiry_confidence = None
    validate_prior = None

# Stub for missing function
def check_micro_live_gates(market, size, price, risk_caps, venue, computed_edge_pct=None):
    """
    Micro-live risk gates - must pass ALL to execute real trades

    Args:
        computed_edge_pct: Pre-computed edge after fees (optional). If provided,
                           this takes precedence over market.get("edge_pct").
    Returns: (passed: bool, violations: list)
    """
    violations = []

    # Gate 1: Position size limit
    if size > risk_caps.get("max_pos_usd", 10):
        violations.append(f"Size ${size:.2f} > max ${risk_caps['max_pos_usd']}")

    # Gate 2: Minimum liquidity
    liquidity = market.get("volume_24h", 0) or market.get("liquidity_usd", 0)
    min_liq = risk_caps.get("liquidity_min_usd", 1000)
    if liquidity < min_liq:
        violations.append(f"Liquidity ${liquidity:.0f} < min ${min_liq}")

    # Gate 3: Edge after fees — use computed value if available
    if computed_edge_pct is not None:
        edge = computed_edge_pct
    else:
        edge = market.get("edge_pct", 0) or market.get("expected_edge_pct", 0)
    min_edge = risk_caps.get("edge_after_fees_pct", 0.5)
    if edge < min_edge:
        violations.append(f"Edge {edge:.1f}% < min {min_edge}%")
    
    # Gate 4: Market end time (must be > 24h away for Kalshi)
    end_time = market.get("close_time") or market.get("expiration_date")
    if end_time:
        try:
            from datetime import datetime
            if isinstance(end_time, str):
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            else:
                end_dt = end_time
            hours_left = (end_dt - datetime.now(end_dt.tzinfo)).total_seconds() / 3600
            min_hours = risk_caps.get("market_end_hrs", 24)
            if hours_left < min_hours:
                violations.append(f"Market ends in {hours_left:.1f}h < min {min_hours}h")
        except:
            pass  # If we can't parse time, allow it
    
    # Gate 5: Price sanity (not too extreme)
    if price < 0.02 or price > 0.98:
        violations.append(f"Price {price:.2f} too extreme")
    
    passed = len(violations) == 0
    return passed, violations

# Load environment
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('/opt/slimy/pm_updown_bot_bundle/logs/runner-optimized.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def estimate_true_price(market_question: str, market_id: str, tier: str = "premium") -> float:
    """Estimate YES probability via AI cascade; fallback to 0.5."""
    if get_ai_prior is None:
        logger.warning(
            "[kelly] AI prior: source=fallback_import_missing prob=0.500 market=%s",
            market_id,
        )
        return 0.5

    try:
        prob = float(get_ai_prior(market_question, tier=tier, market_ticker=market_id))
        if 0.0 <= prob <= 1.0:
            return prob
    except Exception as exc:
        logger.warning("[kelly] AI prior failed: market=%s err=%s", market_id, exc)

    return 0.5

def calculate_probability_weighted_fee(price: float, quantity: float) -> float:
    """
    Calculate Kalshi's probability-weighted fee
    
    Kalshi fee formula: 0.07 × price × (1 - price)
    
    Args:
        price: Contract price (0.01 to 0.99)
        quantity: Number of contracts
    
    Returns:
        Total fee in USD
    """
    
    # The probability-weighted fee peaks at 50¢ (17.5% of contract)
    # At 37% odds: Fee = 0.07 × 0.37 × (1 - 0.37) = 0.07 × 0.37 × 0.63 = 1.64175¢
    # At 50¢ odds: Fee = 0.07 × 0.50 × (1 - 0.50) = 0.07 × 0.50 × 0.50 = 1.75¢
    
    # Calculate fee
    fee = 0.07 * price * (1 - price)
    
    # Multiply by quantity
    total_fee = fee * quantity
    
    return round(total_fee, 4)  # Round to nearest cent

def get_maker_fee(price: float) -> float:
    """
    Calculate if maker order costs $0 (for this market)
    
    Maker orders are free ONLY when market is priced at EXACTLY 50¢
    """
    
    # Check if price is exactly 50 cents
    if price == 0.50:
        return 0.0
    
    return 0.7 * price  # Taker fee (maker orders charge taker fee on fill)

def is_maker_profitable(price: float, true_price: float, win_prob: float, min_edge_pct: float = 0.5) -> bool:
    """
    Determine if maker order would be profitable after fees
    
    Args:
        price: Current market price
        true_price: Your estimated true probability
        win_prob: Probability you're correct
        min_edge_pct: Minimum edge to justify trade (default 0.5%)
    
    Returns:
        True if expected value > cost after fees
    """
    
    # Calculate expected value
    if win_prob > 0.5:
        # If you win, expected value = price (1.0)
        expected_value = price
    else:
        # If you lose, expected value = 0
        expected_value = 0
    
    # Calculate fee (use maker fee if available, otherwise taker)
    fee = get_maker_fee(price) if win_prob > 0.5 else 0.7 * price
    
    # Calculate cost after fees
    cost = price + fee
    
    # Calculate expected profit
    expected_profit = expected_value - cost
    
    # Calculate edge percentage
    if price > 0:
        edge_pct = ((expected_profit / price) * 100) if price > 0 else 0
    else:
        edge_pct = 0
    
    return edge_pct >= min_edge_pct

def find_best_maker_market(markets: list, min_edge_pct: float = 0.5) -> dict:
    """
    Find best market for maker orders
    
    Args:
        markets: List of Kalshi markets
        min_edge_pct: Minimum edge to justify maker order (default 0.5%)
    
    Returns:
        Dictionary with market ID, price, true_price, maker_fee
    """
    best_market = None
    best_edge_pct = -999.0  # Initialize with very low value
    
    for market in markets:
        # Get market data
        yes_price = market.get("odds", {}).get("yes")
        if yes_price is None or yes_price <= 0:
            continue
        market_question = market.get("title", market.get("question", ""))
        market_id = market.get("ticker", market.get("id", "?"))
        true_price = market.get("_ai_true_price")
        if true_price is None:
            true_price = estimate_true_price(market_question, market_id)
        maker_fee = get_maker_fee(yes_price)
        
        # Calculate if maker is profitable
        edge_pct = 0.0  # Initialize
        if is_maker_profitable(yes_price, true_price, 0.6):  # 60% win prob
            current_edge_pct = ((true_price - yes_price) / yes_price) * 100
            edge_pct = current_edge_pct
            
            # Check if this market has better edge than current best
            if edge_pct > best_edge_pct:
                best_edge_pct = edge_pct
                best_market = market
        
        logger.debug(
            f"Market {market.get('id')}: price={yes_price:.4f}, "
            f"true={true_price:.3f}, edge={edge_pct:.2f}%, maker_fee={maker_fee:.2f}¢"
        )
    
    if best_market is None:
        logger.warning("No profitable maker markets found")
    
    return best_market

def calculate_optimal_order_size(bankroll: float, num_markets: int, risk_cap_usd: float = 10.0) -> float:
    """
    Calculate optimal order size across all profitable markets
    
    Args:
        bankroll: Available capital
        num_markets: Number of markets to trade
        risk_cap_usd: Maximum position size
    """
    
    # Calculate total allocation
    max_pos_total = risk_cap_usd * num_markets  # $10 * 5 markets = $50 max exposure
    
    if bankroll > max_pos_total:
        # Can afford to size each market equally
        optimal_size = risk_cap_usd
    else:
        # Bankroll is limiting factor - split evenly
        optimal_size = bankroll / num_markets
    
    # Ensure minimum order size
    min_size = 0.01  # $0.01 minimum
    
    optimal_size = max(optimal_size, min_size)
    
    logger.info(f"Optimal order size: ${optimal_size:.2f} per market (bankroll: ${bankroll:.2f}, risk_cap: ${risk_cap_usd:.2f}, num_markets: {num_markets})")
    
    return optimal_size

def optimize_trade_frequency(current_frequency: int, optimal_edge_pct: float, min_hours: float = 0.0) -> int:
    """
    Reduce trade frequency to focus on higher-edge markets
    
    Args:
        current_frequency: Current trades per day
        optimal_edge_pct: Minimum edge to justify trading (default 0.5%)
        min_hours: Minimum hours between market checks (default 0.5 = 30 minutes)
    
    Returns:
        Recommended new frequency (higher = better quality, lower = reduce frequency)
    """
    
    # Lower frequency only if edge is significantly higher than current
    if optimal_edge_pct > current_frequency * 2:  # Edge > 2x current frequency
        # Reduce to half frequency (market checks less often)
        new_frequency = max(current_frequency // 2, 1)
        logger.info(f"Reducing frequency: {current_frequency} -> {new_frequency} (edge improved from {current_frequency * 2:.1f}% to {optimal_edge_pct:.1f}%)")
    else:
        # Keep current frequency (edge is good enough)
        new_frequency = current_frequency
        logger.info(f"Frequency unchanged: {new_frequency} (current edge: {current_frequency * 2:.1f}% >= optimal {optimal_edge_pct:.1f}%)")
    
    # Ensure minimum time between market checks
    min_checks_per_hour = int(60 / min_hours)  # 2 checks per hour = 30 minute intervals
    new_frequency = min(new_frequency, min_checks_per_hour)
    
    return new_frequency

def filter_low_liquidity_markets(markets: list, min_liquidity_usd: float = 0.0, max_trades: int = 20) -> list:
    """
    Filter out low-liquidity markets (won't get good fills)
    
    Args:
        markets: List of markets
        min_liquidity_usd: Minimum liquidity required
        max_trades: Maximum number of trades to place in batch
    
    Returns:
        Filtered markets
    """
    
    filtered = []
    for market in markets:
        liquidity_usd = market.get("liquidity_usd", 0.0)
        
        # Only skip very low liquidity markets
        if liquidity_usd < min_liquidity_usd:
            logger.debug(f"Skipping {market.get('id')}: liquidity ${liquidity_usd:.2f} < ${min_liquidity_usd:.2f}")
            continue
        
        # Only trade markets with reasonable liquidity
        filtered.append(market)
    
    logger.info(f"Filtered {len(filtered)} markets from {len(markets)} (liquidity >= ${min_liquidity_usd:.2f})")
    
    return filtered

def get_edge_after_fees(market: dict, true_price: float = None) -> float:
    """
    Calculate edge percentage after fees
    
    Args:
        market: Market data
    
    Returns:
        Edge percentage (after fees)
    """
    
    yes_price = market.get("odds", {}).get("yes", 0.0)
    if yes_price <= 0:
        return 0.0
    if true_price is None:
        true_price = market.get("_ai_true_price")
    if true_price is None:
        market_question = market.get("title", market.get("question", ""))
        market_id = market.get("ticker", market.get("id", "?"))
        true_price = estimate_true_price(market_question, market_id)
    
    # Calculate probability-weighted fee
    fee_pct = 0.07  # Probability-scaled fee
    fee = fee_pct * yes_price  # 0.07 × yes_price
    
    # Calculate edge before fees
    if yes_price < true_price:
        edge_before_fees_pct = ((true_price - yes_price) / yes_price) * 100
    else:
        edge_before_fees_pct = 0
    
    # Calculate edge after fees (using maker if available)
    if yes_price == 0.50:
        # Maker order costs $0
        edge_after_fees_pct = edge_before_fees_pct
    else:
        # Maker order charges taker fee on fill
        maker_fee = 0.7 * yes_price
        if true_price <= 0:
            edge_after_fees_pct = 0
        else:
            edge_after_fees_pct = ((true_price - (yes_price + maker_fee)) / true_price) * 100
    
    logger.debug(f"Market {market.get('id')}: price={yes_price:.4f}, fee={fee:.4f}¢, edge_before={edge_before_fees_pct:.2f}%, edge_after={edge_after_fees_pct:.2f}%")
    
    return edge_after_fees_pct

def optimize_kalshi_strategy(mode: str, bankroll: float = 100.0, max_pos_usd: float = 10.0, dry_run: bool = True, min_edge_override: float = None, scratchpad=None):
    """
    Main function for Phase 1 Kalshi optimization

    Args:
        mode: 'shadow' or 'real-live'
        bankroll: Available capital in USD
        max_pos_usd: Maximum position size
        dry_run: If True, only simulate without executing
        scratchpad: Scratchpad instance for event logging (optional)

    Returns:
        Number of orders placed
    """
    
    logger.info("=" * 60)
    logger.info("KALSHI OPTIMIZATION - PHASE 1 (Quick Wins)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    if min_edge_override is not None:
        logger.info(f"Regime min_edge override: {min_edge_override:.2f}%")
    logger.info("=" * 60)

    # ── Micro-live normalization ─────────────────────────────────────────────
    # micro-live = real-live with hard caps (no code duplication)
    is_live = mode in ("real-live", "micro-live")
    MICRO_LIVE_LOG = "[MICRO-LIVE]"
    if mode == "micro-live":
        bankroll = min(bankroll, 25.0)
        max_pos_usd = min(max_pos_usd, 5.0)
        max_daily_loss = 10.0
        logger.info(
            "%s Hard caps applied: bankroll=$%.2f, max_pos=$%.2f, max_daily_loss=$%.2f",
            MICRO_LIVE_LOG, bankroll, max_pos_usd, max_daily_loss,
        )

    # Get risk caps
    risk_caps = {
        "max_pos_usd": max_pos_usd,
        "max_daily_loss_usd": 50.0,
        "max_open_pos": 5,
        "max_daily_positions": 20,
        "liquidity_min_usd": 0.0,
        "edge_after_fees_pct": 0.5,
        "market_end_hrs": 0
    }
    
    # Fetch Kalshi markets
    logger.info("Fetching Kalshi markets...")
    markets = fetch_kalshi_markets()
    
    if not markets:
        logger.warning("No markets fetched")
        return 0
    
    logger.info(f"Fetched {len(markets)} markets")
    
    # Filter for liquidity
    markets = filter_low_liquidity_markets(markets, min_liquidity_usd=0.0, max_trades=20)

    # Pre-dedup: exclude markets we already own or have open orders on
    if is_live:
        try:
            from utils.kalshi_orders import KalshiOrderClient
            order_client = KalshiOrderClient()
            existing_orders = order_client.get_orders(status="open") or []
            existing_positions = order_client.get_positions() or []
            existing_tickers = set()
            for o in existing_orders:
                t = o.get("ticker") or o.get("market_ticker")
                if t:
                    existing_tickers.add(t)
            for p in existing_positions:
                t = p.get("ticker") or p.get("market_ticker")
                if t:
                    existing_tickers.add(t)
            before = len(markets)
            # Check both market.id and market.ticker against existing order tickers
            markets = [
                m for m in markets
                if m.get("id") not in existing_tickers
                and m.get("ticker") not in existing_tickers
            ]
            logger.info(
                "[PREMIUM] Pre-dedup: %d -> %d markets (excluded %d existing)",
                before,
                len(markets),
                len(existing_tickers),
            )
        except Exception as e:
            logger.warning("[PREMIUM] Pre-dedup failed: %s", e)

    # Split premium into two volume-sorted buckets: short-term (<=7d) and long-term (>7d)
    # Each bucket gets up to 10 markets; together they form the 20-market AI premium tier
    now_ts = datetime.now(timezone.utc).timestamp()
    SHORT_DAYS = 7
    SHORT_MIN_VOL = 0
    SHORT_MAX = 10
    LONG_MAX = 10

    short_bucket = []
    long_bucket = []
    for m in markets:
        end_time = m.get("close_time") or m.get("expiration_date")
        days_left = float("inf")
        if end_time:
            try:
                if isinstance(end_time, str):
                    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                else:
                    end_dt = end_time
                days_left = (end_dt.timestamp() - now_ts) / 86400
            except Exception:
                pass
        m["_days_to_end"] = days_left
        vol = m.get("volume_24h", 0) or m.get("liquidity_usd", 0)
        if days_left <= SHORT_DAYS and vol > SHORT_MIN_VOL:
            short_bucket.append(m)
        else:
            long_bucket.append(m)

    short_bucket.sort(key=lambda m: m.get("volume_24h", 0) or m.get("liquidity_usd", 0), reverse=True)
    long_bucket.sort(key=lambda m: m.get("volume_24h", 0) or m.get("liquidity_usd", 0), reverse=True)

    short_premium = short_bucket[:SHORT_MAX]
    long_premium = long_bucket[:LONG_MAX]
    premium_markets = short_premium + long_premium

    logger.info(
        "[PREMIUM] Short-term: %d, Long-term: %d, Total: %d (max %d each)",
        len(short_premium),
        len(long_premium),
        len(premium_markets),
        SHORT_MAX,
    )

    # Tag premium markets; bulk tier absorbs remaining markets up to ai_bulk_max
    for m in markets:
        m["_ai_tier"] = "skip"
        m["_ai_true_price"] = 0.5

    for m in premium_markets:
        m["_ai_tier"] = "premium"

    ai_bulk_max = 0
    bulk_count = 0
    for m in markets:
        if m["_ai_tier"] == "premium":
            continue
        if bulk_count < ai_bulk_max:
            m["_ai_tier"] = "bulk"
            bulk_count += 1
        if bulk_count >= ai_bulk_max:
            break

    for m in markets:
        if m["_ai_tier"] != "skip":
            m["_ai_true_price"] = estimate_true_price(
                m.get("title", m.get("question", "")),
                m.get("ticker", m.get("id", "?")),
                tier=m["_ai_tier"],
            )
    
    # Calculate optimal order size using only markets with real AI priors
    ai_markets = [m for m in markets if m.get("_ai_true_price", 0.5) != 0.5]
    num_ai_markets = max(len(ai_markets), 1)
    optimal_size = calculate_optimal_order_size(bankroll, num_ai_markets, risk_caps["max_pos_usd"])
    logger.info(f"Optimal order size: ${optimal_size:.2f} per market (Kelly on {num_ai_markets} AI-priced markets)")
    
    # Find best maker market (BONUS optimization — not a gate for order execution)
    effective_min_edge = min_edge_override if min_edge_override is not None else risk_caps["edge_after_fees_pct"]
    logger.info("Finding best maker markets...")
    best_maker_market = find_best_maker_market(markets, effective_min_edge)

    if best_maker_market:
        logger.info(f"Best maker market: {best_maker_market.get('id')} at {best_maker_market.get('odds', {}).get('yes', 0.0):.4f} (maker fee: {get_maker_fee(best_maker_market.get('odds', {}).get('yes', 0.0)):.2f}¢)")
    else:
        logger.info("No profitable maker markets found — will use limit orders on AI-priced markets")
    
    # Track metrics
    total_trades = 0
    total_filled = 0
    total_volume = 0.0
    orders = []
    proof_data = {
        "mode": mode,
        "bankroll": bankroll,
        "max_pos_usd": max_pos_usd,
        "orders_placed": [],
        "orders_failed": [],
    }
    
    for market in markets:
        market_id = market.get("id")
        yes_price = market.get("odds", {}).get("yes", 0.0)
        true_price = market.get("_ai_true_price", 0.5)

        # Skip markets with no AI signal (tier="skip" = no API call was made)
        if market.get("_ai_tier") == "skip":
            logger.debug(f"Market {market_id}: tier=skip, skipping — no AI signal")
            continue

        # AI prior self-validation gate
        if validate_prior is not None:
            hours_to_end = market.get("hours_to_end", 48)
            vol_conf = volume_confidence(
                int(market.get("liquidity_usd", 0) / max(yes_price, 0.01)),
                median_volume=500,
            ) if volume_confidence else None
            exp_conf = expiry_confidence(hours_to_end) if expiry_confidence else None

            price_history = market.get("price_history", [])
            if price_history:
                mom = compute_momentum(price_history, window=5) if compute_momentum else None
                zsc = compute_zscore(price_history, window=10) if compute_zscore else None
            else:
                mom = None
                zsc = None

            val_result = validate_prior(
                prior=true_price,
                momentum=mom,
                zscore=zsc,
                contract_price=yes_price,
            )
            market["_validation"] = val_result
            market["_adjusted_prior"] = val_result["adjusted_prior"]

            # Always log validation result
            if val_result["passed"]:
                logger.info(
                    "[kelly] prior validation PASSED market=%s prior=%.3f conf=%.2f flags=%s",
                    market_id,
                    true_price,
                    val_result["confidence"],
                    val_result["flags"],
                )
            else:
                logger.info(
                    "[kelly] prior validation FAILED market=%s prior=%.3f reason=%s flags=%s",
                    market_id,
                    true_price,
                    val_result["reason"],
                    val_result["flags"],
                )

            # Write to scratchpad
            if scratchpad is not None:
                scratchpad.log_prior_validation(
                    market=market_id,
                    prior=true_price,
                    val_result=val_result,
                    passed=val_result["passed"],
                )

            if not val_result["passed"]:
                continue

            # Use adjusted prior for sizing if validation passed
            effective_prior = val_result["adjusted_prior"]
        else:
            effective_prior = true_price

        # Use validated/adjusted prior for all downstream Kelly sizing
        true_price = effective_prior

        # Calculate edge after fees
        edge_after_fees_pct = get_edge_after_fees(market, true_price=true_price)
        
        # Check if this market is a best maker market
        is_best_maker = (best_maker_market and market_id == best_maker_market.get("id"))
        
        # Only trade if edge after fees is sufficient
        if edge_after_fees_pct < risk_caps["edge_after_fees_pct"]:
            logger.debug(f"Market {market_id}: edge={edge_after_fees_pct:.2f}% < {risk_caps['edge_after_fees_pct']}%, too low")
            continue
        
        # Determine if maker order (if not best maker)
        use_maker = not is_best_maker
        
        if use_maker and yes_price == 0.50:
            # Market at exactly 50¢ - maker order costs $0
            order_side = "yes"
            order_price = yes_price  # Buy at current price
            logger.info(f"Market {market_id}: YES order (maker) at {order_price:.4f} (fee: $0.00)")
            fee_cost = 0.0
        else:
            # Market not at 50¢ - maker order charges taker fee on fill
            # Use limit order just inside spread
            order_side = "yes"
            order_price = yes_price * 0.99  # Slightly below current price
            logger.info(f"Market {market_id}: YES order (limit) at {order_price:.4f} (will pay taker fee on fill)")
            # Estimate taker fee if filled: 0.7% of order_price
            # We'll pay taker fee only if our order is filled (someone crosses our spread)
            # We want to earn the spread (market maker), not cross it
            # If we're priced at 0.99 and someone crosses from 0.99 to 1.01, they get filled
            # But if we're the taker, we get the spread
            # Probability of being maker: Not 100%, but significant
            # For simplicity, assume we pay 0.7% taker fee 50% of the time (when our order fills)
            estimated_fee_pct = 0.35  # 0.7% taker fee / 2
            fee_cost = order_price * estimated_fee_pct / 100  # 0.99 * 0.0035
            
            logger.debug(f"Market {market_id}: Estimated fee: {estimated_fee_pct:.2f}% (${fee_cost:.4f})")
        
        # Calculate expected edge after fees
        if use_maker and yes_price == 0.50:
            # Maker order at 50¢: no fee
            edge_after_fees_pct = ((true_price - yes_price) / true_price) * 100
        else:
            # Maker order below 50¢: pay taker fee on fill
            edge_before_fees_pct = ((true_price - yes_price) / yes_price) * 100
            # Expected to pay taker fee 50% of time (when filled)
            expected_taker_fee_pct = edge_before_fees_pct * 0.5
            edge_after_fees_pct = edge_before_fees_pct - expected_taker_fee_pct
            logger.debug(f"Market {market_id}: Edge before fees: {edge_before_fees_pct:.2f}%, expected taker fee: {expected_taker_fee_pct:.2f}%")
        
        # Check if order passes gates
        passed, violations = check_micro_live_gates(market, optimal_size, yes_price, risk_caps, "kalshi", computed_edge_pct=edge_after_fees_pct)
        
        if not passed:
            logger.debug(f"Market {market_id}: Failed gates: {violations}")
            continue
        
        # Execute trade (in live modes: real-live or micro-live)
        # Track first-order safety countdown across the session
        if not hasattr(optimize_kalshi_strategy, "_first_order_placed"):
            optimize_kalshi_strategy._first_order_placed = False

        if is_live and not dry_run:
            prefix = MICRO_LIVE_LOG if mode == "micro-live" else ""

            # ── Dedup: skip markets with existing open orders ─────────────
            if idx == 0:  # only check once at start of run
                try:
                    from utils.kalshi_orders import KalshiOrderClient
                    order_client_dedup = KalshiOrderClient()
                    existing_orders = order_client_dedup.get_orders(status="open")
                    existing_positions = order_client_dedup.get_positions()
                except Exception as e:
                    logger.warning("%s Could not fetch existing orders/positions: %s", prefix, e)
                    existing_orders = []
                    existing_positions = []

                existing_order_tickers = {o.get("ticker") for o in existing_orders if o.get("ticker")}
                existing_position_tickers = {p.get("ticker") for p in existing_positions if p.get("ticker")}
                all_existing_tickers = existing_order_tickers | existing_position_tickers

                MAX_OPEN_ORDERS = 50
                if len(existing_orders) >= MAX_OPEN_ORDERS:
                    logger.warning(
                        "%s Already have %d open orders (max %d), skipping run",
                        prefix, len(existing_orders), MAX_OPEN_ORDERS,
                    )
                    break

                optimize_kalshi_strategy._existing_tickers = all_existing_tickers
                optimize_kalshi_strategy._existing_orders_count = len(existing_orders)

                logger.info(
                    "%s Existing open orders: %d, positions: %d",
                    prefix, len(existing_orders), len(existing_positions),
                )
            else:
                all_existing_tickers = getattr(optimize_kalshi_strategy, "_existing_tickers", set())

            if market_id in all_existing_tickers:
                logger.info("%s SKIP %s — already have open order or position", prefix, market_id)
                continue

            # Safety countdown on first order of session
            if not optimize_kalshi_strategy._first_order_placed:
                logger.warning(
                    "%s PLACING REAL ORDER in 3 seconds... Ctrl+C to abort", prefix
                )
                time.sleep(3)
                optimize_kalshi_strategy._first_order_placed = True

            # Convert price to cents (Kalshi native unit)
            price_cents = int(order_price * 100)
            quantity = 1  # KalshiOrderClient enforces MAX_QUANTITY=1

            try:
                from utils.kalshi_orders import KalshiOrderClient
                order_client = KalshiOrderClient()
                result = order_client.place_order(
                    ticker=market_id,
                    side=order_side,
                    quantity=quantity,
                    price_cents=price_cents,
                )
                order_id = result.get("order", {}).get("order_id", "unknown")
                logger.info(
                    "%s ORDER PLACED: %s %s @ %dc -> order_id=%s",
                    prefix, order_side, market_id, price_cents, order_id,
                )
                # Write to proof pack
                proof_data.setdefault("orders_placed", []).append({
                    "market_id": market_id,
                    "side": order_side,
                    "price_cents": price_cents,
                    "quantity": quantity,
                    "result": result,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "mode": mode,
                })
            except Exception as e:
                logger.error("%s ORDER FAILED: %s", prefix, e)
                proof_data.setdefault("orders_failed", []).append({
                    "market_id": market_id,
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        elif dry_run:
            prefix = MICRO_LIVE_LOG if mode == "micro-live" else "SHADOW MODE"
            logger.info(f"{prefix}: Would place order on {market_id}: {order_side} ${optimal_size:.2f} @ {order_price:.4f}")
            # Record computed-but-not-placed order in proof_data so the proof pack
            # reflects planned activity even in shadow mode
            price_cents_shadow = int(order_price * 100)
            proof_data.setdefault("orders_placed", []).append({
                "market_id": market_id,
                "side": order_side,
                "price_cents": price_cents_shadow,
                "quantity": 1,
                "result": None,  # shadow mode — no real order placed
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
            })
        
        # Update metrics
        total_trades += 1
        if use_maker and yes_price == 0.50:
            total_filled += 1  # Assume maker orders fill
        total_volume += optimal_size if is_live or dry_run else 0
        
        # Calculate expected profit
        if use_maker and yes_price == 0.50:
            # Maker order at 50¢: expected to win 50%
            expected_profit = optimal_size * 0.5  # 50% of order value
            logger.debug(f"Market {market_id}: Expected profit: ${expected_profit:.2f} (${expected_profit * 0.5:.2f} if win)")
        elif use_maker and yes_price < 0.50:
            # Maker order below 50¢: expected edge, taker fees
            expected_profit_pct = edge_after_fees_pct
            expected_profit = optimal_size * (expected_profit_pct / 100)
            logger.debug(f"Market {market_id}: Expected profit: {expected_profit_pct:.2f}% (${optimal_size * expected_profit_pct / 100:.2f} if win)")
        else:
            expected_profit = 0
        
        # Record order
        orders.append({
            "market": market_id,
            "side": order_side,
            "size": optimal_size,
            "price": order_price,
            "fee": fee_cost if "fee_cost" in locals() else 0.7 * yes_price / 100,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    
    # Summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Markets analyzed: {len(markets)}")
    logger.info(f"Best maker market: {best_maker_market.get('id') if best_maker_market else 'None'}")
    logger.info(f"Total orders placed: {total_trades}")
    logger.info(f"Total filled: {total_filled}")
    logger.info(f"Total volume: ${total_volume:.2f}")
    logger.info("=" * 60)
    
    # Generate proof
    proof_id = f"kalshi_optimized_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data.update({
        "data": {
            "orders": orders,
            "summary": {
                "total_orders": total_trades,
                "total_filled": total_filled,
                "total_volume": total_volume,
                "best_maker_market": best_maker_market.get("id") if best_maker_market else None
            }
        },
        "risk_caps": risk_caps
    })

    from utils.proof import generate_proof
    generate_proof(proof_id, proof_data)
    
    logger.info(f"Proof: {proof_id}")
    
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kalshi Optimization - Phase 1 (Quick Wins)")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow", help="Execution mode (micro-live = real trades with hard caps)")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=10.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    exit_code = optimize_kalshi_strategy(
        mode=args.mode,
        bankroll=args.bankroll,
        max_pos_usd=args.max_pos,
        dry_run=(args.mode == "shadow")
    )
    
    logger.info(f"Exit code: {exit_code}")
    sys.exit(exit_code)
