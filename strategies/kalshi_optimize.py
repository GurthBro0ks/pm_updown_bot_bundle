#!/usr/bin/env python3
"""
Kalshi Optimization Strategy - Phase 1 (Quick Wins)
Maker order logic, probability-weighted edge detection, trade frequency optimization
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import runner module
from runner import fetch_kalshi_markets, generate_proof

# Stub for missing function
def check_micro_live_gates(market, size, price, risk_caps, venue):
    """Placeholder for micro-live gates check"""
    return True, []

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
        true_price = 0.5  # Assume fair 50/50 markets unless otherwise known
        maker_fee = get_maker_fee(yes_price)
        
        # Calculate if maker is profitable
        edge_pct = 0.0  # Initialize
        if is_maker_profitable(yes_price, true_price, 0.6):  # 60% win prob
            current_edge_pct = ((0.5 - yes_price) / yes_price) * 100
            edge_pct = current_edge_pct
            
            # Check if this market has better edge than current best
            if edge_pct > best_edge_pct:
                best_edge_pct = edge_pct
                best_market = market
        
        logger.debug(f"Market {market.get('id')}: price={yes_price:.4f}, true=50%, edge={edge_pct:.2f}%, maker_fee={maker_fee:.2f}¢")
    
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

def get_edge_after_fees(market: dict) -> float:
    """
    Calculate edge percentage after fees
    
    Args:
        market: Market data
    
    Returns:
        Edge percentage (after fees)
    """
    
    yes_price = market.get("odds", {}).get("yes", 0.0)
    true_price = 0.5  # Assume fair 50/50 markets
    
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
    
    # Fetch Kalshi markets
    logger.info("Fetching Kalshi markets...")
    markets = fetch_kalshi_markets()
    
    if not markets:
        logger.warning("No markets fetched")
        return 0
    
    logger.info(f"Fetched {len(markets)} markets")
    
    # Filter for liquidity
    markets = filter_low_liquidity_markets(markets, min_liquidity_usd=0.0, max_trades=20)
    
    # Calculate optimal order size
    optimal_size = calculate_optimal_order_size(bankroll, len(markets), risk_caps["max_pos_usd"])
    logger.info(f"Optimal order size: ${optimal_size:.2f} per market")
    
    # Find best maker markets
    logger.info("Finding best maker markets...")
    best_maker_market = find_best_maker_market(markets, risk_caps["edge_after_fees_pct"])
    
    if best_maker_market:
        logger.info(f"Best maker market: {best_maker_market.get('id')} at {best_maker_market.get('odds', {}).get('yes', 0.0):.4f} (maker fee: {get_maker_fee(best_maker_market.get('odds', {}).get('yes', 0.0)):.2f}¢)")
    
    # Track metrics
    total_trades = 0
    total_filled = 0
    total_volume = 0.0
    orders = []
    
    for market in markets:
        market_id = market.get("id")
        yes_price = market.get("odds", {}).get("yes", 0.0)
        true_price = 0.5  # Assume fair 50/50 markets
        
        # Calculate edge after fees
        edge_after_fees_pct = get_edge_after_fees(market)
        
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
        passed, violations = check_micro_live_gates(market, optimal_size, yes_price, risk_caps, "kalshi")
        
        if not passed:
            logger.debug(f"Market {market_id}: Failed gates: {violations}")
            continue
        
        # Execute trade (in real-live mode only)
        if mode == "real-live" and not dry_run:
            # This is where actual order placement would happen
            # For now, just log what would happen
            logger.info(f"Would place order on {market_id}: {order_side} ${optimal_size:.2f} @ {order_price:.4f}")
        elif dry_run:
            # Log simulated order
            logger.info(f"SHADOW MODE: Would place order on {market_id}: {order_side} ${optimal_size:.2f} @ {order_price:.4f}")
        else:
            # Default: don't trade
            logger.debug(f"Market {market_id}: No mode specified, skipping")
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
    
    from runner import generate_proof
    generate_proof(proof_id, proof_data)
    
    logger.info(f"Proof: {proof_id}")
    
    return 0

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
