#!/usr/bin/env python3
"""
Kalshi Optimization Strategy - Phase 1 (Quick Wins)
Maker order logic, probability-weighted edge detection, trade frequency optimization
Fixed: Added sys import
"""

import sys
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import runner module
from runner import fetch_kalshi_markets, generate_proof, check_micro_live_gates

# Load environment
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
    """
    
    fee = 0.07 * price * (1 - price)
    total_fee = fee * quantity
    
    return round(total_fee, 4)  # Round to nearest cent

def get_maker_fee(price: float) -> float:
    """
    Calculate if maker order costs $0 (for this market)
    
    Maker orders are free ONLY when market is priced at EXACTLY 50 cents
    """
    
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
    
    if win_prob > 0.5:
        expected_value = price
    else:
        expected_value = 0
    
    fee = get_maker_fee(price) if win_prob > 0.5 else 0.07 * price
    
    cost = price + fee
    
    expected_profit = expected_value - cost
    
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
    best_edge_pct = -999.0
    
    for market in markets:
        yes_price = market.get("odds", {}).get("yes", 0.0)
        true_price = 0.5
        maker_fee = get_maker_fee(yes_price)
        
        if is_maker_profitable(yes_price, true_price, 0.6):
            current_edge_pct = ((0.5 - yes_price) / yes_price) * 100
            edge_pct = current_edge_pct
            
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
    
    max_pos_total = risk_cap_usd * num_markets
    
    if bankroll > max_pos_total:
        optimal_size = risk_cap_usd
    else:
        optimal_size = bankroll / num_markets
    
    min_size = 0.01
    optimal_size = max(optimal_size, min_size)
    
    logger.info("Optimal order size: {size:.2f} per market (bankroll: ${bankroll:.2f}, risk_cap: ${risk_cap_usd:.2f}, num_markets: {n})".format(
        size=optimal_size,
        bankroll=bankroll,
        risk_cap=risk_cap_usd,
        n=num_markets
    ))
    
    return optimal_size

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
    logger.info("Mode: {}".format(mode))
    logger.info("Bankroll: ${:.2f}".format(bankroll))
    logger.info("Max position: ${:.2f}".format(max_pos_usd))
    logger.info("=" * 60)
    
    risk_caps = {
        "max_pos_usd": max_pos_usd,
        "max_daily_loss_usd": 50,
        "max_open_pos": 5,
        "max_daily_positions": 20,
        "liquidity_min_usd": 0.0,
        "edge_after_fees_pct": 0.5,
        "market_end_hrs": 0
    }
    
    logger.info("Fetching Kalshi markets...")
    markets = fetch_kalshi_markets()
    
    if not markets:
        logger.warning("No markets fetched")
        return 0
    
    logger.info("Fetched {} markets".format(len(markets)))
    
    logger.info("Filtering for liquidity...")
    markets = filter_low_liquidity_markets(markets, min_liquidity_usd=0.0, max_trades=20)
    
    logger.info("Filtered {} markets".format(len(markets)))
    
    optimal_size = calculate_optimal_order_size(bankroll, len(markets), risk_caps["max_pos_usd"])
    logger.info("Optimal order size: ${:.2f} per market".format(optimal_size))
    
    logger.info("Finding best maker markets...")
    best_maker_market = find_best_maker_market(markets, risk_caps["edge_after_fees_pct"])
    
    if best_maker_market:
        logger.info("Best maker market: {} at {} (maker fee: {} cents)".format(
            best_maker_market.get('id'),
            best_maker_market.get('odds', {}).get('yes', 0.0),
            get_maker_fee(best_maker_market.get('odds', {}).get('yes', 0.0))
        ))
    
    total_trades = 0
    total_filled = 0
    total_volume = 0.0
    orders = []
    
    for market in markets:
        market_id = market.get("id")
        yes_price = market.get("odds", {}).get("yes", 0.0)
        true_price = 0.5
        
        edge_after_fees_pct = get_edge_after_fees(market)
        
        is_best_maker = (best_maker_market and market_id == best_maker_market.get("id"))
        
        if edge_after_fees_pct < risk_caps["edge_after_fees_pct"]:
            logger.debug("Market {}: edge={:.2f}% too low".format(market_id, edge_after_fees_pct))
            continue
        
        use_maker = not is_best_maker
        
        if use_maker and yes_price == 0.50:
            order_side = "yes"
            order_price = yes_price
            logger.info("Market {}: YES order (maker) at {} (fee: $0.00)".format(
                market_id, order_price
            ))
            fee_cost = 0.0
        else:
            order_side = "yes"
            order_price = yes_price * 0.99
            logger.info("Market {}: YES order (limit) at {} (will pay taker fee on fill)".format(
                market_id, order_price
            ))
            estimated_fee_pct = edge_after_fees_pct * 0.5
            fee_cost = order_price * estimated_fee_pct / 100
            logger.info("Market {}: Estimated fee: {}% (${:.4f}) if use_maker else 0.7 * yes_price / 100".format(
                market_id, estimated_fee_pct, fee_cost
            ))
        
        passed, violations = check_micro_live_gates(market, optimal_size, yes_price, risk_caps, "kalshi")
        
        if not passed:
            logger.debug("Market {}: Failed gates: {}".format(market_id, violations))
            continue
        
        if mode == "real-live" and not dry_run:
            logger.info("Would place order on {}: {} ${:.2f} @ {}".format(
                market_id, order_side, optimal_size, order_price
            ))
        elif dry_run:
            logger.info("SHADOW MODE: Would place order on {}: {} ${:.2f} @ {}".format(
                market_id, order_side, optimal_size, order_price
            ))
        else:
            continue
        
        total_trades += 1
        if use_maker and yes_price == 0.50:
            total_filled += 1
        total_volume += optimal_size
        
        orders.append({
            "market": market_id,
            "side": order_side,
            "size": optimal_size,
            "price": order_price,
            "fee": fee_cost if "fee_cost" in locals() else 0.07 * order_price / 100,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("Markets analyzed: {}".format(len(markets)))
    logger.info("Best maker market: {}".format(best_maker_market.get('id') if best_maker_market else None))
    logger.info("Total orders placed: {}".format(total_trades))
    logger.info("Total filled: {}".format(total_filled))
    logger.info("Total volume: ${:.2f}".format(total_volume))
    logger.info("=" * 60)
    
    proof_id = "kalshi_optimized_{}".format(datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
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
    
    logger.info("Proof: {}".format(proof_id))
    
    return total_trades

def get_edge_after_fees(market: dict) -> float:
    """
    Calculate edge percentage after fees
    
    Args:
        market: Market data
    
    Returns:
        Edge percentage (after fees)
    """
    
    yes_price = market.get("odds", {}).get("yes", 0.0)
    true_price = 0.5
    fee_pct = 0.07
    fee = fee_pct * yes_price
    
    if yes_price < true_price:
        edge_before_fees_pct = ((true_price - yes_price) / yes_price) * 100
    else:
        edge_before_fees_pct = 0
    
    if yes_price == 0.50:
        edge_after_fees_pct = edge_before_fees_pct
    else:
        estimated_taker_fee_pct = edge_before_fees_pct * 0.5
        edge_after_fees_pct = edge_before_fees_pct - estimated_taker_fee_pct
    
    return edge_after_fees_pct

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
        
        if liquidity_usd < min_liquidity_usd:
            logger.debug("Skipping {}: liquidity ${:.2f} < ${:.2f}".format(
                market.get('id'), liquidity_usd, min_liquidity_usd
            ))
            continue
        
        filtered.append(market)
    
    logger.info("Filtered {} markets from {} (liquidity >= ${:.2f})".format(
        len(filtered), len(markets), min_liquidity_usd
    ))
    
    return filtered

def main():
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
    
    logger.info("Exit code: {}".format(exit_code))
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
