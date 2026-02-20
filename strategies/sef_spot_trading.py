#!/usr/bin/env python3
"""
SEF Spot Trading Module - Phase 2
Uniswap V3, dYdX, GMX V2 (Spot Trading Only - No Derivatives)
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import requests
from web3 import Web3
from eth_account import Account

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Load environment
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('/opt/slimy/pm_updown_bot_bundle/logs/sef-spot-trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# SEF Configuration
SEF_CONFIGS = {
    "uniswap_v3": {
        "name": "Uniswap V3",
        "chain": "ethereum",
        "rpc_url": os.getenv("UNISWAP_RPC_URL", "https://mainnet.infura.io/v3/YOUR_KEY"),
        "router_address": "0xE592427A0AEce92De3Edee1F18E0157C05861562",  # Uniswap V3 Router
        "min_trade_usd": 0.01,
        "slippage_pct": 0.5
    },
    "dydx_v4": {
        "name": "dYdX v4",
        "chain": "ethereum",
        "rpc_url": os.getenv("DYDX_RPC_URL", "https://mainnet.infura.io/v3/YOUR_KEY"),
        "api_url": "https://api.dydx.exchange/v4",
        "min_trade_usd": 0.01,
        "slippage_pct": 0.5
    },
    "gmx_v2": {
        "name": "GMX V2",
        "chain": "arbitrum",
        "rpc_url": os.getenv("GMX_RPC_URL", "https://arb1.arbitrum.io/rpc"),
        "api_url": "https://api.gmx.io/v2",
        "min_trade_usd": 0.01,
        "slippage_pct": 0.5
    }
}

RISK_CAPS = {
    "max_pos_usd": 20,
    "max_daily_loss_usd": 20,
    "max_open_pos": 3,
    "max_daily_trades": 15,
    "slippage_max_pct": 1.0
}

def check_sef_micro_live_gates(order, risk_caps):
    """
    Micro-live risk gates for SEF trading
    
    Returns: (passed: bool, violations: list)
    """
    violations = []
    
    # Gate 1: Position size limit
    if order["size_usd"] > risk_caps.get("max_pos_usd", 20):
        violations.append(f"Size ${order['size_usd']:.2f} > max ${risk_caps['max_pos_usd']}")
    
    # Gate 2: Minimum profit
    if order["profit_pct"] < 0.5:  # At least 0.5% profit
        violations.append(f"Profit {order['profit_pct']:.2f}% < min 0.5%")
    
    # Gate 3: Slippage check
    if order.get("slippage", 0) > risk_caps.get("slippage_max_pct", 1.0):
        violations.append(f"Slippage {order.get('slippage', 0):.2f}% > max {risk_caps['slippage_max_pct']}")
    
    passed = len(violations) == 0
    return passed, violations

def get_uniswap_price(token_in, token_out, amount_in):
    """Get Uniswap V3 price for spot trade"""
    try:
        uniswap_router = SEF_CONFIGS["uniswap_v3"]["router_address"]
        
        # For now, return mock price (actual Uniswap V3 ABI implementation needed)
        # Would call uniswap_v3_router.exactInputSingle()
        price_mock = 1.0  # 1:1 ratio
        
        logger.info(f"Uniswap V3: {token_in} -> {token_out} @ {price_mock}")
        return price_mock
    except Exception as e:
        logger.error(f"Uniswap price fetch error: {str(e)}")
        return 1.0

def get_dydx_price(token_in, token_out, amount_in):
    """Get dYdX v4 spot price"""
    try:
        dydx_api_url = SEF_CONFIGS["dydx_v4"]["api_url"]
        
        # dYdX API call for oracle price
        params = {
            "market": f"{token_in}-{token_out}",
            "resolution": "3600"
        }
        
        # Mock API call (actual dYdX API integration needed)
        price_mock = 1.0
        
        logger.info(f"dYdX v4: {token_in} -> {token_out} @ {price_mock}")
        return price_mock
    except Exception as e:
        logger.error(f"dYdX price fetch error: {str(e)}")
        return 1.0

def get_gmx_price(token_in, token_out, amount_in):
    """Get GMX V2 spot price"""
    try:
        gmx_api_url = SEF_CONFIGS["gmx_v2"]["api_url"]
        
        # GMX API call for spot price
        # Mock API call (actual GMX API integration needed)
        price_mock = 1.0
        
        logger.info(f"GMX V2: {token_in} -> {token_out} @ {price_mock}")
        return price_mock
    except Exception as e:
        logger.error(f"GMX price fetch error: {str(e)}")
        return 1.0

def find_sef_arbitrage_opportunity(token_in, token_out):
    """
    Find arbitrage opportunity across SEFs (Uniswap, dYdX, GMX)
    Only for spot trading (no derivatives)
    """
    prices = {}
    
    # Get prices from all SEFs
    prices["uniswap"] = get_uniswap_price(token_in, token_out, 1.0)
    prices["dydx"] = get_dydx_price(token_in, token_out, 1.0)
    prices["gmx"] = get_gmx_price(token_in, token_out, 1.0)
    
    # Find best price
    best_exchange = min(prices, key=prices.get)
    best_price = prices[best_exchange]
    worst_exchange = max(prices, key=prices.get)
    worst_price = prices[worst_exchange]
    
    # Calculate arbitrage
    price_spread_pct = ((best_price - worst_price) / worst_price) * 100
    gross_spread = best_price - worst_price
    
    # Estimate gas costs (Arbitrum: $0.50, Ethereum: $10.00)
    gas_costs_eth = 10.0
    gas_costs_arb = 0.50
    
    # Calculate net profit after gas
    if best_exchange in ["gmx"]:
        # Best price on Arbitrum - cheaper gas
        net_profit = gross_spread - gas_costs_arb
    else:
        # Best price on Ethereum - expensive gas
        net_profit = gross_spread - gas_costs_eth
    
    net_profit_pct = (net_profit / worst_price) * 100
    
    logger.info(f"Arb opportunity: {token_in} -> {token_out}")
    logger.info(f"  Best: {best_exchange} @ {best_price:.4f}")
    logger.info(f"  Worst: {worst_exchange} @ {worst_price:.4f}")
    logger.info(f"  Spread: {gross_spread:.4f} ({price_spread_pct:.2f}%)")
    logger.info(f"  Net: ${net_profit:.2f} ({net_profit_pct:.2f}%)")
    
    # Only profitable if net > gas
    if net_profit > 0:
        return {
            "token_in": token_in,
            "token_out": token_out,
            "best_exchange": best_exchange,
            "best_price": best_price,
            "worst_exchange": worst_exchange,
            "worst_price": worst_price,
            "spread": gross_spread,
            "spread_pct": price_spread_pct,
            "net_profit": net_profit,
            "net_profit_pct": net_profit_pct,
            "profitable": True,
            "chain": "arbitrum" if best_exchange == "gmx" else "ethereum"
        }
    else:
        logger.info(f"  No arbitrage (gas cost exceeds spread)")
        return None

def optimize_sef_strategy(bankroll, max_pos_usd, mode="shadow"):
    """
    Main function for SEF spot trading optimization
    """
    logger.info("=" * 60)
    logger.info("SEF SPOT TRADING - PHASE 2")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
    # Tokens to monitor (ETH-USD, BTC-USD)
    trading_pairs = [
        {"token_in": "WETH", "token_out": "USDC"},
        {"token_in": "WBTC", "token_out": "USDC"}
    ]
    
    # Find arbitrage opportunities
    opportunities = []
    for pair in trading_pairs:
        arb = find_sef_arbitrage_opportunity(pair["token_in"], pair["token_out"])
        if arb:
            opportunities.append(arb)
    
    logger.info(f"Found {len(opportunities)} arbitrage opportunities")
    
    # Calculate optimal order size
    if len(opportunities) > 0:
        optimal_size = bankroll / len(opportunities)
        optimal_size = max(optimal_size, 0.01)
        optimal_size = min(optimal_size, max_pos_usd)
        
        logger.info(f"Optimal order size: ${optimal_size:.2f} per opportunity")
    else:
        optimal_size = 0.0
        logger.info("No opportunities found")
    
    # Generate orders (in shadow mode for now)
    orders = []
    total_volume = 0.0
    
    for arb in opportunities:
        # Risk checks
        if arb["net_profit"] < RISK_CAPS["slippage_max_pct"]:
            logger.debug(f"Skipping {arb['best_exchange']}: spread too small")
            continue
        
        if optimal_size < SEF_CONFIGS[arb["best_exchange"]]["min_trade_usd"]:
            logger.debug(f"Skipping {arb['best_exchange']}: size below minimum")
            continue
        
        # Create order
        order = {
            "token_in": arb["token_in"],
            "token_out": arb["token_out"],
            "exchange": arb["best_exchange"],
            "price": arb["best_price"],
            "size_usd": optimal_size,
            "profit_usd": arb["net_profit"],
            "profit_pct": arb["net_profit_pct"],
            "chain": arb["chain"],
            "mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Micro-live gate check
        if mode == "micro-live" or mode == "real-live":
            passed, violations = check_sef_micro_live_gates(order, RISK_CAPS)
            if not passed:
                logger.info(f"SEF order failed gates: {violations}")
                continue
            logger.info(f"SEF order passed all gates")
        
        orders.append(order)
        total_volume += optimal_size
    
    # Summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Trading pairs: {len(trading_pairs)}")
    logger.info(f"Opportunities: {len(opportunities)}")
    logger.info(f"Total orders: {len(orders)}")
    logger.info(f"Total volume: ${total_volume:.2f}")
    
    if len(opportunities) > 0:
        best_opp = max(opportunities, key=lambda x: x.get("profit_pct", 0) if isinstance(x, dict) else 0)
        logger.info(f"Best opportunity: {best_opp.get('exchange')} ({best_opp.get('token_in')} -> {best_opp.get('token_out')})")
        logger.info(f"  Profit: ${best_opp.get('profit_usd', 0):.2f} ({best_opp.get('profit_pct', 0):.2f}%)")
    
    logger.info("=" * 60)
    
    # Generate proof
    proof_id = f"sef_spot_trading_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": mode,
        "bankroll": bankroll,
        "max_pos_usd": max_pos_usd,
        "data": {
            "orders": orders,
            "opportunities": opportunities,
            "summary": {
                "trading_pairs": len(trading_pairs),
                "opportunities": len(opportunities),
                "total_orders": len(orders),
                "total_volume": total_volume
            }
        },
        "risk_caps": RISK_CAPS
    }
    
    # Import generate_proof from runner module
    from runner import generate_proof
    generate_proof(proof_id, proof_data)
    
    logger.info(f"Proof: {proof_id}")
    
    return len(orders)

def main():
    parser = argparse.ArgumentParser(description="SEF Spot Trading - Phase 2")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow", help="Execution mode (micro-live = real trades with risk gates)")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=20.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Import logging configuration from runner
    try:
        import logging
        logger.info("SEF spot trading module loaded")
    except ImportError:
        pass
    
    result = optimize_sef_strategy(
        mode=args.mode,
        bankroll=args.bankroll,
        max_pos_usd=args.max_pos
    )
    
    logger.info(f"Exit code: {result}")
    return result

if __name__ == "__main__":
    sys.exit(main())
