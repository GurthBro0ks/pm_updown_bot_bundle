#!/usr/bin/env python3
"""
Kalshi Optimization Strategy - Phase 1 (Quick Wins)
Maker order logic, probability-weighted edge detection, trade frequency optimization
Fixed: No syntax errors, proper f-string handling
"""

import sys
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

from runner import fetch_kalshi_markets, generate_proof, check_micro_live_gates

load_dotenv()

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
    fee = 0.07 * price * (1 - price)
    total_fee = fee * quantity
    return round(total_fee, 4)

def get_maker_fee(price: float) -> float:
    if price == 0.50:
        return 0.0
    return 0.07 * price

def is_maker_profitable(price: float, true_price: float, win_prob: float, min_edge_pct: float = 0.5) -> bool:
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
        
        logger.debug("Market {}: price={:.4f}, true=50%, edge={:.2f}%, maker_fee={:.2f} cents".format(
            market.get('id'), yes_price, edge_pct, maker_fee
        ))
    
    if best_market is None:
        logger.warning("No profitable maker markets found")
    
    return best_market

def calculate_optimal_order_size(bankroll: float, num_markets: int, risk_cap_usd: float = 10.0) -> float:
    max_pos_total = risk_cap_usd * num_markets
    
    if bankroll > max_pos_total:
        optimal_size = risk_cap_usd
    else:
        optimal_size = bankroll / num_markets
    
    min_size = 0.01
    optimal_size = max(optimal_size, min_size)
    
    logger.info("Optimal order size: ${:.2f} per market (bankroll: ${:.2f}, risk_cap: ${:.2f}, num_markets: {})".format(
        optimal_size, bankroll, risk_cap_usd, num_markets
    ))
    
    return optimal_size

def get_edge_after_fees(market: dict) -> float:
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
    
    logger.debug("Market {}: price={:.4f}, fee={:.4f} cents, edge_before={:.2f}%, edge_after={:.2f}%".format(
        market.get('id'), yes_price, fee, edge_before_fees_pct, edge_after_fees_pct
    ))
    
    return edge_after_fees_pct

def optimize_kalshi_strategy(mode: str, bankroll: float, max_pos_usd: float, dry_run: bool = True):
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
    
    markets = filter_low_liquidity_markets(markets, min_liquidity_usd=0.0, max_trades=20)
    
    logger.info("Filtered {} markets".format(len(markets)))
    
    optimal_size = calculate_optimal_order_size(bankroll, len(markets), risk_caps["max_pos_usd"])
    logger.info("Optimal order size: ${:.2f} per market".format(optimal_size))
    
    logger.info("Finding best maker markets...")
    best_maker_market = find_best_maker_market(markets, risk_caps["edge_after_fees_pct"])
    
    if best_maker_market:
        logger.info("Best maker market: {} at {:.4f} (maker fee: {:.2f} cents)".format(
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
            logger.info("Market {}: YES order (maker) at {:.4f} (fee: $0.00)".format(
                market_id, order_price
            ))
            fee_cost = 0.0
        else:
            order_side = "yes"
            order_price = yes_price * 0.99
            logger.info("Market {}: YES order (limit) at {:.4f} (will pay taker fee on fill)".format(
                market_id, order_price
            ))
            estimated_fee_pct = 0.35
            fee_cost = order_price * estimated_fee_pct / 100
            logger.info("Market {}: Estimated fee: {:.2f}% (${:.4f})".format(
                market_id, estimated_fee_pct, fee_cost
            ))
        
        passed, violations = check_micro_live_gates(market, optimal_size, yes_price, risk_caps, "kalshi")
        
        if not passed:
            logger.debug("Market {}: Failed gates: {}".format(market_id, violations))
            continue
        
        if mode == "real-live" and not dry_run:
            logger.info("Would place order on {}: {} ${:.2f} @{:.4f}".format(
                market_id, order_side, optimal_size, order_price
            ))
        elif dry_run:
            logger.info("SHADOW MODE: Would place order on {}: {} ${:.2f} @{:.4f}".format(
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
            "fee": fee_cost,
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
    
    from runner import generate_proof
    generate_proof(proof_id, proof_data)
    
    logger.info("Proof: {}".format(proof_id))
    
    return 1

if __name__ == "__main__":
    import argparse
    
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
