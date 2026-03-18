#!/usr/bin/env python3
"""
Kalshi Optimization Strategy - Phase 1 (Quick Wins)
Maker order logic, probability-weighted edge detection, trade frequency optimization
Fixed: No syntax errors, simplified logging
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import runner module
from runner import fetch_kalshi_markets, generate_proof, check_micro_live_gates

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
    
    Kalshi fee formula: 0.07 * price * (1 - price)
    
    Args:
        price: Contract price (0.01 to 0.99)
        quantity: Number of contracts
    
    Returns:
        Total fee in USD
    """
    
    # Calculate fee
    fee = 0.07 * price * (1 - price)
    
    # Multiply by quantity
    total_fee = fee * quantity
    
    return round(total_fee, 4)

def get_maker_fee(price: float) -> float:
    """
    Calculate if maker order costs $0 (for this market)
    
    Maker orders are free ONLY when market is priced at EXACTLY 50 cents
    """
    
    # Check if price is exactly 50 cents
    if price == 0.50:
        return 0.0
    
    return 0.07 * price

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
        expected_value = price
    else:
        expected_value = 0
    
    # Calculate fee (use maker fee if available, otherwise taker fee)
    fee = get_maker_fee(price) if win_prob > 0.5 else 0.07 * price
    
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
        yes_price = market.get("odds", {}).get("yes", 0.0)
        true_price = 0.5  # Assume fair 50/50 markets unless otherwise known
        maker_fee = get_maker_fee(yes_price)
        
        # Calculate if maker is profitable
        if is_maker_profitable(yes_price, true_price, 0.6):
            current_edge_pct = ((0.5 - yes_price) / yes_price) * 100
            edge_pct = current_edge_pct
            
            # Check if this market has better edge than current best
            if edge_pct > best_edge_pct:
                best_edge_pct = edge_pct
                best_market = market
        
        logger.debug("Market {id}: price={price:.4f}, true=50%, edge={edge:.2f}%, maker_fee={fee:.2f} cents".format(
            id=market.get('id'),
            price=yes_price,
            edge=edge_pct,
            fee=maker_fee
        ))
    
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
    
    Returns:
        Optimal order size
    """
    
    # Calculate total allocation
    max_pos_total = risk_cap_usd * num_markets
    
    if bankroll > max_pos_total:
        # Can afford to size each market equally
        optimal_size = risk_cap_usd
    else:
        # Bankroll is limiting factor - split evenly
        optimal_size = bankroll / num_markets
    
    # Ensure minimum order size
    min_size = 0.01  # $0.01 minimum
    
    optimal_size = max(optimal_size, min_size)
    
    logger.info("Optimal order size: {size:.2f} per market (bankroll: ${bankroll:.2f}, risk_cap: ${risk_cap_usd:.2f}, num_markets: {n})".format(
        size=optimal_size,
        bankroll=bankroll,
        risk_cap=risk_cap_usd,
        n=num_markets
    ))
    
    return optimal_size

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
    fee = fee_pct * yes_price
    
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
        # Estimate to pay taker fee 50% of time (when our order is filled)
        estimated_taker_fee_pct = edge_before_fees_pct * 0.5
        edge_after_fees_pct = edge_before_fees_pct - estimated_taker_fee_pct
    
    logger.debug("Market {id}: price={price:.4f}, fee={fee:.4f} cents, edge_before={before:.2f}%, edge_after={after:.2f}%".format(
        id=market.get('id'),
        price=yes_price,
        fee=fee,
        before=edge_before_fees_pct,
        after=edge_after_fees_pct
    ))
    
    return edge_after_fees_pct

def optimize_kalshi_strategy(mode: str, bankroll: float, max_pos_usd: float, dry_run: bool = True):
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
    logger.info("Mode: {mode}".format(mode=mode))
    logger.info("Bankroll: ${bankroll:.2f}".format(bankroll=bankroll))
    logger.info("Max position: ${max_pos_usd:.2f}".format(max_pos=max_pos_usd))
    logger.info("=" * 60)
    
    # Get risk caps
    risk_caps = {
        "max_pos_usd": max_pos_usd,
        "max_daily_loss_usd": 50,
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
    
    logger.info("Fetched {n} markets".format(n=len(markets)))
    
    # Filter for liquidity
    markets = filter_low_liquidity_markets(markets, min_liquidity_usd=0.0, max_trades=20)
    
    logger.info("Filtered {n} markets".format(n=len(markets)))
    
    # Calculate optimal order size
    optimal_size = calculate_optimal_order_size(bankroll, len(markets), risk_caps["max_pos_usd"])
    logger.info("Optimal order size: ${size:.2f} per market".format(size=optimal_size))
    
    # Find best maker markets
    logger.info("Finding best maker markets...")
    best_maker_market = find_best_maker_market(markets, risk_caps["edge_after_fees_pct"])
    
    if best_maker_market:
        logger.info("Best maker market: {id} at {price:.4f} (maker fee: {fee:.2f} cents)".format(
            id=best_maker_market.get('id'),
            price=best_maker_market.get('odds', {}).get('yes', 0.0),
            fee=get_maker_fee(best_maker_market.get('odds', {}).get('yes', 0.0))
        ))
    else:
        logger.info("No profitable maker markets found")
    
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
            logger.debug("Market {id}: edge={edge:.2f}% too low".format(id=market_id, edge=edge_after_fees_pct))
            continue
        
        # Determine if maker order (if not best maker)
        use_maker = not is_best_maker
        
        if use_maker and yes_price == 0.50:
            # Market at exactly 50 cents - maker order costs $0
            order_side = "yes"
            order_price = yes_price  # Buy at current price
            logger.info("Market {id}: YES order (maker) at {price:.4f} (fee: $0.00)".format(
                id=market_id,
                price=order_price
            ))
            fee_cost = 0.0
        else:
            # Market not at 50 cents - maker order charges taker fee on fill
            # Use limit order just inside spread
            order_side = "yes"
            order_price = yes_price * 0.99  # Slightly below current price
            # Estimate to pay taker fee 50% of time (when filled)
            estimated_taker_fee_pct = edge_after_fees_pct * 0.5
            fee_cost = order_price * estimated_taker_fee_pct / 100  # 0.99 * 0.0035
            logger.info("Market {id}: YES order (limit) at {price:.4f} (est. fee: {fee:.2f}%)".format(
                id=market_id,
                price=order_price,
                fee=estimated_taker_fee_pct
            ))
        
        # Check if order passes gates
        passed, violations = check_micro_live_gates(market, optimal_size, yes_price, risk_caps, "kalshi")
        
        if not passed:
            logger.debug("Market {id}: Failed gates: {v}".format(id=market_id, v=violations))
            continue
        
        # Execute trade (in real-live mode only)
        if mode == "real-live" and not dry_run:
            # This is where actual order placement would happen
            logger.info("Would place order on {id}: {side} {size:.2f} @{price:.4f}".format(
                id=market_id,
                side=order_side,
                size=optimal_size,
                price=order_price
            ))
        elif dry_run:
            # Log simulated order
            logger.info("SHADOW MODE: Would place order on {id}: {side} {size:.2f} @{price:.4f}".format(
                id=market_id,
                side=order_side,
                size=optimal_size,
                price=order_price
            ))
        else:
            # Default: don't trade
            logger.debug("Market {id}: No mode specified, skipping".format(id=market_id))
            continue
        
        # Update metrics
        total_trades += 1
        if use_maker and yes_price == 0.50:
            total_filled += 1  # Assume maker orders fill
        total_volume += optimal_size if mode == "real-live" or dry_run else 0
        
        # Calculate expected profit
        if use_maker and yes_price == 0.50:
            # Maker order at 50 cents: expected to win 50%
            expected_profit = optimal_size * 0.5  # 50% of order value
            logger.debug("Market {id}: Expected profit: ${profit:.2f} (if win) ({pct:.0f}%)".format(
                id=market_id,
                profit=expected_profit,
                pct=expected_profit/optimal_size*100
            ))
        elif use_maker and yes_price < 0.50:
            # Maker order below 50 cents: expected edge, taker fees
            expected_profit_pct = edge_after_fees_pct
            expected_profit = optimal_size * (expected_profit_pct / 100)
            logger.debug("Market {id}: Expected profit: ${profit:.2f} ({pct:.2f}%)".format(
                id=market_id,
                profit=expected_profit,
                pct=expected_profit_pct
            ))
        else:
            expected_profit = 0.0
        
        # Record order
        orders.append({
            "market": market_id,
            "side": order_side,
            "size": optimal_size,
            "price": order_price,
            "fee": fee_cost,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    
    # Summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("Markets analyzed: {n}".format(n=len(markets)))
    logger.info("Best maker market: {id}".format(id=best_maker_market.get('id') if best_maker_market else None))
    logger.info("Total orders placed: {n}".format(n=total_trades))
    logger.info("Total filled: {n}".format(n=total_filled))
    logger.info("Total volume: ${vol:.2f}".format(vol=total_volume))
    logger.info("=" * 60)
    
    # Generate proof
    proof_id = "kalshi_optimized_{timestamp}".format(
        timestamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    )
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
    
    generate_proof(proof_id, proof_data)
    
    logger.info("Proof: {id}".format(id=proof_id))
    
    logger.info("Phase 1 optimization complete - {n} orders would be placed".format(n=total_trades))
    
    return total_trades

def main():
    parser = argparse.ArgumentParser(description="Kalshi Optimization - Phase 1 (Quick Wins)")
    parser.add_argument("--mode", choices=["shadow", "real-live"], default="shadow", help="Execution mode")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=10.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    result = optimize_kalshi_strategy(
        mode=args.mode,
        bankroll=args.bankroll,
        max_pos_usd=args.max_pos,
        dry_run=(args.mode == "shadow")
    )
    
    logger.info("Exit code: {code}".format(code=result))
    
    return result

if __name__ == "__main__":
    main()
