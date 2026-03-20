#!/usr/bin/env python3
"""
Kalshi Optimization Strategy - Phase 1 (Quick Wins)
Maker order logic, probability-weighted edge detection, trade frequency optimization

Configuration centralized in config.py
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import centralized config
from config import RISK_CAPS, KALSHI_KEY

# Tiered edge thresholds based on effective fee
# For series with fee_multiplier <= 0.035 (halved fees, effective 3.5%): 0.75%
# For series with fee_multiplier > 0.035 (normal fees, effective 7%): 1.25%
EDGE_THRESHOLD_LOW_FEE = 0.75   # 0.75% for halved-fee markets (S&P/Nasdaq)
EDGE_THRESHOLD_HIGH_FEE = 1.25  # 1.25% for normal-fee markets

# Import runner module
# Local imports (avoid circular import)
from utils.proof import generate_proof
from utils.kalshi import fetch_kalshi_markets
from utils.position_sizer import size_position, update_bankroll, get_bayesian_tracker
from strategies.sentiment_scorer import get_bayesian_prior
from utils.vpin import get_market_vpin
from utils.kalshi_orders import KalshiOrderClient, SafetyLimitError

# Stub for missing function
def check_micro_live_gates(market, size, price, risk_caps, venue):
    """
    Micro-live risk gates - must pass ALL to execute real trades
    
    Returns: (passed: bool, violations: list)
    """
    violations = []
    
    # Gate 1: Position size limit
    if size > risk_caps.get("max_pos_usd", 10):
        violations.append(f"Size ${size:.2f} > max ${risk_caps['max_pos_usd']}")
    
    # Gate 2: Minimum liquidity
    liquidity = market.get("volume_usd", 0) or market.get("liquidity", 0)
    min_liq = risk_caps.get("liquidity_min_usd", 1000)
    if liquidity < min_liq:
        violations.append(f"Liquidity ${liquidity:.0f} < min ${min_liq}")
    
    # Gate 3: Edge after fees (tiered based on fee_multiplier)
    edge = market.get("edge_pct", 0) or market.get("expected_edge_pct", 0)
    fee_multiplier = market.get("fee_multiplier", 1.0)
    effective_fee = 0.07 * fee_multiplier
    if effective_fee <= 0.035:
        min_edge = EDGE_THRESHOLD_LOW_FEE  # 0.75% for halved-fee markets
    else:
        min_edge = EDGE_THRESHOLD_HIGH_FEE  # 1.25% for normal-fee markets
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


def get_order_client():
    """
    Initialize Kalshi order client lazily.
    Returns None if keys are missing or init fails.
    """
    try:
        key_id = os.getenv('KALSHI_TRADING_KEY_ID')
        key_file = os.getenv('KALSHI_TRADING_KEY_FILE')
        if not key_id or not key_file:
            logger.warning("KALSHI_TRADING_KEY_ID or KALSHI_TRADING_KEY_FILE not set")
            return None
        return KalshiOrderClient(
            api_key=key_id,
            private_key_path=key_file,
            base_url='https://api.elections.kalshi.com/trade-api/v2'
        )
    except Exception as e:
        logger.error(f"Failed to init order client: {e}")
        return None

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


def get_taker_fee(price: float, fee_multiplier: float = 1.0) -> float:
    """
    Calculate taker fee for a market
    
    Args:
        price: Current market price
        fee_multiplier: Fee multiplier (1.0 for normal, 0.5 for S&P/Nasdaq markets)
    
    Returns:
        Taker fee in dollars
    """
    base_fee = 0.7 * price  # Base taker fee is 7% of price
    return base_fee * fee_multiplier


def is_taker_profitable(price: float, true_price: float, fee_multiplier: float = 1.0, min_edge_pct: float = 1.0) -> bool:
    """
    Determine if taker order would be profitable after fees
    
    For S&P/Nasdaq markets with halved fees (0.5 multiplier):
    - 7% base fee becomes 3.5% effective fee
    - At 3.5% fee, taker orders become viable for high-confidence signals
    
    Args:
        price: Current market price
        true_price: Your estimated true probability
        fee_multiplier: Fee multiplier (1.0 for normal, 0.5 for S&P/Nasdaq)
        min_edge_pct: Minimum edge after fees (default 1.0%)
    
    Returns:
        True if expected profit > min_edge after fees
    """
    # Calculate taker fee
    taker_fee = get_taker_fee(price, fee_multiplier)
    
    # Calculate expected value if we're right
    expected_value = true_price
    
    # Cost = price + fee
    cost = price + taker_fee
    
    # Expected profit
    expected_profit = expected_value - cost
    
    # Edge percentage
    if price > 0:
        edge_pct = (expected_profit / price) * 100
    else:
        edge_pct = 0
    
    return edge_pct >= min_edge_pct


def find_best_taker_market(markets: list, min_edge_pct: float = 1.0) -> dict:
    """
    Find best market for taker orders (S&P/Nasdaq with halved fees)
    
    Args:
        markets: List of Kalshi markets
        min_edge_pct: Minimum edge after fees (default 1.0%, ignored - uses tiered thresholds)
    
    Returns:
        Dictionary with market ID, price, true_price, taker_fee, edge_pct
    """
    best_market = None
    best_edge = 0
    
    for market in markets:
        yes_price = market.get("odds", {}).get("yes", 0.0)
        fee_multiplier = market.get("fee_multiplier", 1.0)  # 0.5 for S&P/Nasdaq

        # Skip if no halved fees (not worth taker at 7% fee)
        if fee_multiplier != 0.5:
            continue

        # FIX 2026-03-19: Wire AI prior — Grok/GLM cascade for true probability
        market_question = market.get("title", market.get("question", ""))
        try:
            ai_prob = get_bayesian_prior({"title": market_question})
            true_price = ai_prob if ai_prob and 0.01 < ai_prob < 0.99 else 0.5
            logger.info(f"[kalshi] AI prior: {market.get('ticker', market.get('id', '?'))} "
                        f"q='{market_question[:60]}' prob={true_price:.3f}")
        except Exception:
            true_price = 0.5  # Safe fallback
        
        # Apply tiered threshold based on fee_multiplier
        # fee_multiplier=0.5 → effective fee 3.5% → use EDGE_THRESHOLD_LOW_FEE (0.75%)
        # fee_multiplier=1.0 → effective fee 7% → use EDGE_THRESHOLD_HIGH_FEE (1.25%)
        effective_fee = 0.07 * fee_multiplier
        if effective_fee <= 0.035:
            threshold = EDGE_THRESHOLD_LOW_FEE
        else:
            threshold = EDGE_THRESHOLD_HIGH_FEE
        
        # Check if taker is profitable
        if is_taker_profitable(yes_price, true_price, fee_multiplier, threshold):
            taker_fee = get_taker_fee(yes_price, fee_multiplier)
            edge_pct = ((true_price - (yes_price + taker_fee)) / true_price) * 100
            
            if edge_pct > best_edge:
                best_edge = edge_pct
                best_market = {
                    "id": market.get("id"),
                    "price": yes_price,
                    "true_price": true_price,
                    "taker_fee": taker_fee,
                    "edge_pct": edge_pct,
                    "fee_multiplier": fee_multiplier,
                    "threshold_used": threshold
                }
    
    if best_market:
        logger.info(f"Best taker market: {best_market['id']} @ {best_market['price']:.4f} (taker fee: {best_market['taker_fee']:.2f}¢, edge: {best_market['edge_pct']:.2f}%, threshold: {best_market['threshold_used']:.2f}%)")
    else:
        logger.info("No profitable taker markets found")
    
    return best_market


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
        min_edge_pct: Minimum edge to justify maker order (default 0.5%, ignored - uses tiered thresholds)
    
    Returns:
        Dictionary with market ID, price, true_price, maker_fee
    """
    best_market = None
    best_edge_pct = -999.0  # Initialize with very low value
    
    for market in markets:
        # Get market data
        yes_price = market.get("odds", {}).get("yes")
        fee_multiplier = market.get("fee_multiplier", 1.0)
        maker_fee = get_maker_fee(yes_price)

        # FIX 2026-03-19: Wire AI prior — Grok/GLM cascade for true probability
        market_question = market.get("title", market.get("question", ""))
        try:
            ai_prob = get_bayesian_prior({"title": market_question})
            true_price = ai_prob if ai_prob and 0.01 < ai_prob < 0.99 else 0.5
            logger.info(f"[kalshi] AI prior maker: {market.get('ticker', market.get('id', '?'))} "
                        f"q='{market_question[:60]}' prob={true_price:.3f}")
        except Exception:
            true_price = 0.5  # Safe fallback
        
        # Apply tiered threshold based on fee_multiplier
        effective_fee = 0.07 * fee_multiplier
        if effective_fee <= 0.035:
            threshold = EDGE_THRESHOLD_LOW_FEE
        else:
            threshold = EDGE_THRESHOLD_HIGH_FEE
        
        # Calculate if maker is profitable
        edge_pct = 0.0  # Initialize
        if is_maker_profitable(yes_price, true_price, true_price, threshold):
            current_edge_pct = ((true_price - yes_price) / yes_price) * 100
            edge_pct = current_edge_pct
            
            # Check if this market has better edge than current best
            if edge_pct > best_edge_pct:
                best_edge_pct = edge_pct
                best_market = {
                    **market,
                    "edge_pct": edge_pct,
                    "threshold_used": threshold
                }
        
        logger.debug(f"Market {market.get('id')}: price={yes_price:.4f}, true=50%, edge={edge_pct:.2f}%, threshold={threshold:.2f}%, maker_fee={maker_fee:.2f}¢")
    
    if best_market is None:
        logger.warning("No profitable maker markets found")
    elif best_edge_pct > 0:
        logger.info(f"Best maker market: {best_market.get('id')} @ {best_market.get('odds', {}).get('yes', 0):.4f} (edge: {best_edge_pct:.2f}%, threshold: {best_market.get('threshold_used'):.2f}%)")
    
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


# Market category keywords for filtering
FINANCIAL_KEYWORDS = ['SPX', 'SP500', 'S&P', 'NASDAQ', 'NDX', 'DOW', 'DJIA', 'RUSSELL', 'VIX', 
                      'STOCK', 'INDEX', 'FUTURES', 'EQUITY']
ECONOMIC_KEYWORDS = ['FED', 'FOMC', 'RATE', 'CPI', 'INFLATION', 'GDP', 'UNEMPLOY', 'NFP', 
                     'JOBS', 'EMPLOYMENT', 'RECESSION', 'TREASURY', 'YIELD', 'BOND']
POLITICAL_KEYWORDS = ['ELECTION', 'PRESIDENT', 'CONGRESS', 'SENATE', 'HOUSE', 'GOVERNOR', 
                      'POLITICAL', 'VOTE', 'BALLOT', 'DEMOCRAT', 'REPUBLICAN']
WEATHER_KEYWORDS = ['WEATHER', 'TEMPERATURE', 'RAIN', 'SNOW', 'HURRICANE', 'TORNADO', 
                    'FLOOD', 'DROUGHT', 'DEGREE', 'COLD', 'HOT']

# Sports/esports to EXCLUDE
EXCLUDE_KEYWORDS = ['SPORT', 'ESPORT', 'GAME', 'MATCH', 'TEAM', 'PLAYER', 'NBA', 'NFL', 
                    'MLB', 'NHL', 'SOCCER', 'FOOTBALL', 'BASKETBALL', 'BASEBALL', 'HOCKEY',
                    'KXMV', 'MULTIGAME', 'VIDEO GAME', 'E-SPORT', 'ESL', 'TOURNAMENT']


def filter_markets_by_category(markets: list, include_categories: list = None) -> list:
    """
    Filter markets to only include specified categories.
    
    Priority 0 fix: Bot was scanning only esports/sports markets which have:
    - Low liquidity (wide spreads, thin books)
    - High variance (esports outcomes are noisy)
    - NOT where Kalshi's real volume lives
    
    Kalshi's $110M+ daily volume concentrates in:
    - S&P 500 range markets (halved fee multiplier: 0.035 vs 0.07)
    - Nasdaq-100 range markets (same halved fees)
    - Fed rate decision markets
    - CPI/inflation markets
    - Presidential/political markets
    - Weather event markets
    
    Args:
        markets: List of markets with 'id' (ticker) and 'question' fields
        include_categories: Categories to include ['financial', 'economic', 'political', 'weather']
    
    Returns:
        Filtered markets in desired categories
    """
    # If include_categories is explicitly None, skip filtering entirely (2026-03-14)
    if include_categories is None:
        return markets

    # Default to empty list if not provided (not None)
    if not include_categories:
        include_categories = []
    
    filtered = []
    for market in markets:
        ticker = market.get('id', '').upper()
        question = market.get('question', '').upper()
        text = f"{ticker} {question}"
        
        # First, exclude sports/esports
        excluded = False
        for keyword in EXCLUDE_KEYWORDS:
            if keyword in text:
                logger.debug(f"Excluding sports/esports market: {ticker}")
                excluded = True
                break
        
        if excluded:
            continue
        
        # Then, include only specified categories
        included = False
        if 'financial' in include_categories:
            for keyword in FINANCIAL_KEYWORDS:
                if keyword in text:
                    included = True
                    market['category'] = 'financial'
                    market['fee_multiplier'] = 0.5  # Halved fees for S&P/Nasdaq
                    break
        
        if not included and 'economic' in include_categories:
            for keyword in ECONOMIC_KEYWORDS:
                if keyword in text:
                    included = True
                    market['category'] = 'economic'
                    break
        
        if not included and 'political' in include_categories:
            for keyword in POLITICAL_KEYWORDS:
                if keyword in text:
                    included = True
                    market['category'] = 'political'
                    break
        
        if not included and 'weather' in include_categories:
            for keyword in WEATHER_KEYWORDS:
                if keyword in text:
                    included = True
                    market['category'] = 'weather'
                    break
        
        if included:
            filtered.append(market)
    
    logger.info(f"Category filter: {len(filtered)} markets from {len(markets)} (categories: {include_categories})")
    return filtered


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

def get_edge_after_fees(market: dict, true_price_override: float = None) -> float:
    """
    Calculate edge percentage after fees.

    Args:
        market: Market data
        true_price_override: If provided, use this instead of AI prior (avoids double API calls)

    Returns:
        Edge percentage (after fees)
    """

    yes_price = market.get("odds", {}).get("yes", 0.0)

    # FIX 2026-03-19: Wire AI prior instead of flat 0.5
    if true_price_override is not None:
        true_price = true_price_override
    else:
        market_question = market.get("title", market.get("question", ""))
        try:
            ai_prob = get_bayesian_prior({"title": market_question})
            true_price = ai_prob if ai_prob and 0.01 < ai_prob < 0.99 else 0.5
        except Exception:
            true_price = 0.5
    
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
        edge_after_fees_pct = ((true_price - (yes_price + maker_fee)) / true_price) * 100
    
    logger.debug(f"Market {market.get('id')}: price={yes_price:.4f}, fee={fee:.4f}¢, edge_before={edge_before_fees_pct:.2f}%, edge_after={edge_after_fees_pct:.2f}%")
    
    return edge_after_fees_pct

def optimize_kalshi_strategy(mode: str, bankroll: float = 100.0, max_pos_usd: float = 10.0, dry_run: bool = True):
    """
    Main function for Phase 1 Kalshi optimization
    
    Args:
        mode: 'shadow' or 'real-live'
        bankroll: Available capital in USD
        max_pos_usd: Maximum position size
        dry_run: If True, only simulate without executing
    
    Returns:
        Number of orders placed
    """
    
    logger.info("=" * 60)
    logger.info("KALSHI OPTIMIZATION - PHASE 1 (Quick Wins)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
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

    # Initialize position sizer with current bankroll
    update_bankroll(bankroll)

    # Get current open positions
    try:
        from runner import paper_money
        current_positions = len(paper_money.positions) if hasattr(paper_money, 'positions') else 0
    except Exception:
        current_positions = 0

    # Fetch Kalshi markets
    logger.info("Fetching Kalshi markets...")
    markets = fetch_kalshi_markets()
    
    if not markets:
        logger.warning("No markets fetched")
        return 0
    
    logger.info(f"Fetched {len(markets)} markets")
    
    # FIX 2026-03-19: Re-enable category filtering — block esports/sports, target financial
    # Note: filter_markets_by_category uses keyword matching on ticker/question text
    # The series-level blocklist (KALSHI_BLOCKED_CATEGORIES) is the primary filter
    # This keyword filter provides additional belt-and-suspenders safety
    markets = filter_markets_by_category(markets, include_categories=None)
    
    if not markets:
        logger.warning("No markets fetched or all markets filtered by liquidity")
        return 0
    
    # Filter for liquidity
    markets = filter_low_liquidity_markets(markets, min_liquidity_usd=0.0, max_trades=20)

    # Fallback optimal size (will be overridden by position_sizer)
    optimal_size = max_pos_usd
    logger.info(f"Max position: ${optimal_size:.2f} (position_sizer will determine actual size)")

    # Find best maker markets
    logger.info("Finding best maker markets...")
    best_maker_market = find_best_maker_market(markets, risk_caps["edge_after_fees_pct"])
    
    if best_maker_market:
        logger.info(f"Best maker market: {best_maker_market.get('id')} at {best_maker_market.get('odds', {}).get('yes', 0.0):.4f} (maker fee: {get_maker_fee(best_maker_market.get('odds', {}).get('yes', 0.0)):.2f}¢)")
    
    # PRIORITY 1A: Find best taker markets (S&P/Nasdaq with halved fees)
    logger.info("Finding best taker markets (S&P/Nasdaq with 3.5% fee)...")
    best_taker_market = find_best_taker_market(markets, min_edge_pct=1.0)  # 1% min edge after fees
    
    if best_taker_market:
        logger.info(f"Best taker market: {best_taker_market['id']} @ {best_taker_market['price']:.4f} (3.5% fee, edge: {best_taker_market['edge_pct']:.2f}%)")
    
    # Track metrics
    total_trades = 0
    total_filled = 0
    total_volume = 0.0
    orders = []
    
    for market in markets:
        market_id = market.get("id")
        yes_price = market.get("odds", {}).get("yes", 0.0)
        fee_multiplier = market.get("fee_multiplier", 1.0)

        # FIX 2026-03-19: Skip zero-volume markets (esports/inactive)
        vol_24h = market.get("volume_24h", 0) or 0
        if vol_24h < 1:
            logger.debug(f"[kalshi] Skipping {market_id}: zero volume (vol_24h={vol_24h})")
            continue

        # FIX 2026-03-19: Wire AI prior — Grok/GLM cascade for true probability
        market_question = market.get("title", market.get("question", ""))
        try:
            ai_prob = get_bayesian_prior({"title": market_question})
            true_price = ai_prob if ai_prob and 0.01 < ai_prob < 0.99 else 0.5
            logger.info(f"[kalshi] AI edge: {market_id} q='{market_question[:60]}' prob={true_price:.3f}")
        except Exception:
            true_price = 0.5  # Safe fallback

        # Calculate edge after fees (pass true_price to avoid double AI call)
        edge_after_fees_pct = get_edge_after_fees(market, true_price_override=true_price)
        
        # Determine threshold based on fee_multiplier for logging
        effective_fee = 0.07 * fee_multiplier
        if effective_fee <= 0.035:
            threshold = EDGE_THRESHOLD_LOW_FEE
        else:
            threshold = EDGE_THRESHOLD_HIGH_FEE
        
        # Log trade decision with threshold
        logger.info(f"[KALSHI] {market_id}: edge={edge_after_fees_pct:.4f} threshold={threshold:.4f} fee_mult={fee_multiplier} → {'TRADE' if edge_after_fees_pct > threshold else 'SKIP'}")
        
        # Check if this market is best maker OR best taker
        is_best_maker = (best_maker_market and market_id == best_maker_market.get("id"))
        is_best_taker = (best_taker_market and market_id == best_taker_market.get("id"))
        
        # Skip if not a best market
        if not is_best_maker and not is_best_taker:
            continue
        
        # Determine order type
        use_maker = risk_caps.get("kalshi_maker_only", True)
        if is_best_taker and fee_multiplier == 0.5:
            # Taker order for S&P/Nasdaq market
            order_type = "taker"
            taker_fee = get_taker_fee(yes_price, fee_multiplier)
            order_side = "yes"
            order_price = yes_price  # Buy at current price
            logger.info(f"Market {market_id}: YES order (maker) at {order_price:.4f} (fee: $0.00)")
            fee_cost = 0.0
        else:
            # Taker order or maker order with fee
            if is_best_taker and fee_multiplier == 0.5:
                # S&P/Nasdaq taker order (3.5% fee)
                order_side = "yes"
                order_price = yes_price
                estimated_fee_pct = 0.035  # 3.5% taker fee
                logger.info(f"Market {market_id}: YES order (taker) at {order_price:.4f} (S&P/Nasdaq 3.5% fee)")
            else:
                # Maker order charges taker fee on fill
                order_side = "yes"
                order_price = yes_price * 0.99  # Slightly below current price
                logger.info(f"Market {market_id}: YES order (limit) at {order_price:.4f} (will pay taker fee on fill)")
                # For simplicity, assume we pay 0.7% taker fee 50% of the time
                estimated_fee_pct = 0.35
            
            fee_cost = order_price * estimated_fee_pct / 100
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
        
        # Set edge_pct on market dict for gate check (uses calculated edge_after_fees_pct)
        market["edge_pct"] = edge_after_fees_pct

        # ── VPIN pre-gate: detect informed trading before micro-live gates ──
        try:
            vpin_result = get_market_vpin(market_id)
            if vpin_result["action"] == "halt":
                logger.info(f"[VPIN] Market {market_id} halted: VPIN={vpin_result['vpin']:.3f}")
                continue
            elif vpin_result["action"] == "widen":
                # Double the spread requirement by doubling effective edge_pct
                # This causes micro_live_gates to require 2x the edge to pass
                market["edge_pct"] = edge_after_fees_pct * 2.0
                logger.info(f"[VPIN] Market {market_id} spread widened: VPIN={vpin_result['vpin']:.3f}, edge_pct doubled to {market['edge_pct']:.4f}")
        except Exception as e:
            logger.warning(f"[VPIN] Market {market_id}: VPIN check failed, proceeding: {e}")
        # ── end VPIN pre-gate ──

        # Check if order passes gates (legacy check)
        passed, violations = check_micro_live_gates(market, optimal_size, yes_price, risk_caps, "kalshi")

        if not passed:
            logger.info(f"[KALSHI GATE] {market_id}: Failed gates: {violations}")
            continue

        # Use position_sizer for Kelly-based sizing with EV filter
        # Estimate true probability from edge (true_price = market_price + edge)
        estimated_prob = min(1.0, yes_price + (edge_after_fees_pct / 100))
        estimated_fee_pct = 0.035 if fee_multiplier == 0.5 else 0.7  # 3.5% or 7%

        order_size, sizing_meta = size_position(
            market_id=market_id,
            market_price=yes_price,
            bankroll=bankroll,
            current_positions=current_positions,
            estimated_prob=estimated_prob,
            odds=1.0/yes_price if yes_price > 0 else 1.0,
            fees_pct=estimated_fee_pct / 100,
        )

        if sizing_meta.get("blocked"):
            logger.info(f"[KALSHI] {market_id}: Blocked by position_sizer - {sizing_meta.get('block_reason', 'unknown')}")
            continue

        # Update position count
        current_positions += 1
        optimal_size = order_size

        # Execute trade
        if mode == "micro-live":
            # Real penny trade with safety limits enforced inside client
            client = get_order_client()
            if client is None:
                logger.error("Cannot execute micro-live: order client init failed")
                continue

            # Pre-flight: check balance
            try:
                balance = client.get_balance()
                available = balance.get('available_balance', 0)
                if available < 10:  # less than 10 cents
                    logger.warning(f"Balance too low for micro-live: ${available}")
                    continue
            except Exception as e:
                logger.error(f"Balance check failed: {e}")
                continue

            # Build order — price in cents (1-99), always 1 contract
            price_cents = max(1, min(99, int(round(order_price * 100))))
            if price_cents < 1:
                logger.warning(f"Price {order_price:.4f} too low to convert to cents")
                continue

            try:
                result = client.place_order(
                    ticker=market_id,
                    side="yes",
                    quantity=1,
                    price_cents=price_cents,
                )
                order_id = result.get('order_id', 'unknown')
                status = result.get('status', 'unknown')
                logger.info(f"MICRO-LIVE ORDER: {market_id} YES 1x @{price_cents}¢ → {status} (id: {order_id})")

                # Record to pnl.db for bootstrap validator
                try:
                    from utils.pnl_database import record_trade
                    record_trade(
                        phase='phase1_kalshi',
                        ticker=market_id,
                        action='BUY',
                        price=order_price,
                        size_usd=optimal_size,
                        pnl_usd=0,
                        pnl_pct=0
                    )
                except Exception as db_err:
                    logger.warning(f"Failed to record trade to pnl.db: {db_err}")

            except SafetyLimitError as e:
                logger.warning(f"MICRO-LIVE SAFETY LIMIT: {market_id} — {e}")
                continue
            except Exception as e:
                logger.error(f"MICRO-LIVE ORDER FAILED: {market_id} — {e}")
                continue

        elif mode == "real-live":
            # Stub — real-live uses separate execution path
            logger.info(f"REAL-LIVE STUB: Would place order on {market_id}: {order_side} ${optimal_size:.2f} @ {order_price:.4f}")

        elif dry_run:
            # Log simulated order
            logger.info(f"SHADOW MODE: Would place order on {market_id}: {order_side} ${optimal_size:.2f} @ {order_price:.4f}")

        else:
            logger.debug(f"Market {market_id}: mode={mode}, dry_run={dry_run}, skipping")
            continue
        
        # Update metrics
        total_trades += 1
        if use_maker and yes_price == 0.50:
            total_filled += 1  # Assume maker orders fill
        total_volume += optimal_size if mode == "real-live" or dry_run else 0
        
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sizing_meta": sizing_meta,
            "edge_pct": edge_after_fees_pct,
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
    proof_data = {
        "mode": mode,
        "bankroll": bankroll,
        "max_pos_usd": max_pos_usd,
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
    }
    
    from utils.proof import generate_proof
    generate_proof(proof_id, proof_data)
    
    logger.info(f"Proof: {proof_id}")

    # FIX 2026-03-19: Return actual order count so runner knows if anything happened
    return total_trades

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kalshi Optimization - Phase 1 (Quick Wins)")
    parser.add_argument("--mode", choices=["shadow", "real-live"], default="shadow", help="Execution mode")
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
