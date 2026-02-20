#!/usr/bin/env python3
"""
Multi-Venue Runner - Shipping Mode
Phase 1: Kalshi Optimization (Complete)
Phase 2: SEF Spot Trading (Complete)
Phase 3: Stock Hunter (In Progress)
"""

import argparse
import json
import logging
import os
import sys
import requests
import base64
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

# Strategy imports
from strategies import kalshi_optimize as kalshi_opt_module
from strategies import sef_spot_trading as sef_opt_module
from strategies import stock_hunter as stock_hunter_module

load_dotenv()

# Setup logging with valid format (no syntax error)
log_format = '%(asctime)s | %(levelname)s | %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler('/opt/slimy/pm_updown_bot_bundle/logs/runner.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

CURRENT_PHASE = "shipping_mode"  # Phase 1 & 2 complete, Phase 3 in progress

RISK_CAPS = {
    "max_pos_usd": 10,
    "max_daily_loss_usd": 50,
    "max_open_pos": 5,
    "max_daily_positions": 20,
    "liquidity_min_usd": 0,
    "edge_after_fees_pct": 0.5,
    "market_end_hrs": 0,
    # Phase 1 (Kalshi Optimization) specific settings
    "kalshi_maker_only": True,
    "kalshi_min_profit_usd": 0.10,
    "kalshi_max_daily_trades": 10,
    "maker_fee_usd": 0.00,
    "kalshi_fee_pct": 0.07,
    "kalshi_true_probability": 0.5,
    # Phase 2 (SEF Spot Trading) specific settings
    "sef_max_pos_usd": 20,
    "sef_max_daily_loss_usd": 20,
    "sef_max_open_pos": 3,
    "sef_max_daily_trades": 15,
    "sef_slippage_max_pct": 1.0,
    "sef_min_spread_pct": 0.5,
    "sef_max_daily_trades": 15,
    "sef_gas_budget_usd": 10.0,
    # Phase 3 (Stock Hunter) specific settings
    "stock_max_pos_usd": 100,
    "stock_max_daily_loss_usd": 30,
    "stock_max_open_pos": 3,
    "stock_max_daily_positions": 10,
    "stock_min_liquidity_usd": 10000,
    "stock_sentiment_threshold": 0.6,
    "stock_min_market_cap_usd": 100000000,
    "stock_max_market_cap_usd": 300000000,
    "stock_min_price_usd": 1.0,
    "stock_max_price_usd": 5.0
}

VENUE_CONFIGS = {
    "kalshi": {
        "name": "Kalshi",
        "min_trade_usd": 0.01,
        "max_trade_usd": 10.0,
        "base_url": "https://api.elections.kalshi.com",
        "settlement": "USDC"
    },
    "polymarket": {
        "name": "Polymarket",
        "min_trade_usd": 0.01,
        "max_trade_usd": 100.0,
        "fee_pct": 0.005,
        "base_url": "https://api.thegraph.com/subgraphs/name/polymarket/markets",
        "settlement": "USDC",
        "wallet_address": os.getenv("POLYMARKET_WALLET"),
        "shadow_mode": True
    },
    "ibkr": {
        "name": "Interactive Brokers",
        "min_trade_usd": 1.00,
        "max_trade_usd": 1000.0,
        "fee_pct": 0.01,
        "base_url": "mock://ibkr",
        "settlement": "USD"
    }
}

PROOF_DIR = Path("/opt/slimy/pm_updown_bot_bundle/proofs")
KELLY_FRAC_SHADOW = 0.25
KELLY_FRAC_LIVE = 0.05

secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
with open(secret_file, 'rb') as f:
    private_key = serialization.load_pem_private_key(f.read(), password=None)

def get_headers(method, path):
    timestamp = str(int(time.time()))
    base_path = path.split('?')[0]
    to_sign = f"{timestamp}\n{method}\n{base_path}"
    signature = private_key.sign(to_sign.encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode('ascii')
    auth_header = f'RSA keyId="{os.getenv("KALSHI_KEY")}",timestamp="{timestamp}",signature="{sig_b64}"'
    return {'Authorization': auth_header}

def generate_proof(proof_id, data):
    proof_path = PROOF_DIR / f"{proof_id}.json"
    with open(proof_path, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"Proof: {proof_path}")

def fetch_kalshi_markets():
    api_key = os.getenv("KALSHI_KEY")
    if not api_key:
        logger.warning("WARNING: No KALSHI_KEY - using mock")
        return [
            {"id": "FED-25.FEB", "question": "Fed rate", "odds": {"yes": 0.72, "no": 0.28}, "liquidity_usd": 25000, "hours_to_end": 720, "fees_pct": 0.01},
            {"id": "CPI-25.FEB", "question": "CPI", "odds": {"yes": 0.55, "no": 0.45}, "liquidity_usd": 50000, "hours_to_end": 48, "fees_pct": 0.01}
        ]
    headers = get_headers('GET', '/v1/markets')
    resp = requests.get('https://api.elections.kalshi.com/trade-api/v2/markets', headers=headers, params={'status': 'open', 'limit': 100}, timeout=10)
    if resp.status_code == 200:
        data = resp.json() if resp.text.strip() else {"markets": []}
        markets = []
        for m in data.get('markets', []):
            ticker = m.get('ticker', '')
            yes_bid_cents = m.get('yes_bid', 0)
            yes_ask_cents = m.get('yes_ask', 0)
            if yes_ask_cents <= 0:
                continue
            yes_bid_cents = m.get('yes_bid', 0)
            yes_ask_cents = m.get('yes_ask', 0)
            yes_price_cents = (yes_bid_cents + yes_ask_cents) / 2
            yes_price = yes_price_cents / 100.0
            no_price = 1.0 - yes_price
            liquidity_usd = m.get('open_interest', 0) * yes_price
            markets.append({
                "id": ticker,
                "question": m.get('short_name', ticker),
                "odds": {"yes": yes_price, "no": no_price},
                "liquidity_usd": liquidity_usd,
                "hours_to_end": 48
            })
        logger.info(f"Fetched {len(markets)} markets")
        return markets
    logger.error("API fail - using mock")
    return []

def run_phase1_kalshi_optimization(mode, bankroll, max_pos_usd):
    """Phase 1: Kalshi Optimization (Complete)"""
    logger.info("=" * 60)
    logger.info("PHASE 1: KALSHI OPTIMIZATION (COMPLETE)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
    try:
        if not hasattr(kalshi_opt_module, 'optimize_kalshi_strategy'):
            logger.error("Kalshi optimization module not found")
            return 0
        
        result = kalshi_opt_module.optimize_kalshi_strategy(
            mode=mode,
            bankroll=bankroll,
            max_pos_usd=max_pos_usd,
            dry_run=(mode == "shadow")
        )
        
        logger.info(f"Phase 1 optimization complete - result: {result}")
        return 1
    except Exception as e:
        logger.error(f"Phase 1 error: {str(e)}")
        return 0

def run_phase2_sef_spot_trading(mode, bankroll, max_pos_usd):
    """Phase 2: SEF Spot Trading (Complete)"""
    logger.info("=" * 60)
    logger.info("PHASE 2: SEF SPOT TRADING (COMPLETE)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
    try:
        if not hasattr(sef_opt_module, 'main'):
            logger.error("SEF spot trading module not found")
            return 0
        
        result = sef_opt_module.main()
        
        logger.info(f"Phase 2 complete - result: {result}")
        return 1
    except Exception as e:
        logger.error(f"Phase 2 error: {str(e)}")
        return 0

def run_phase3_stock_hunter(mode, bankroll, max_pos_usd):
    """Phase 3: Stock Hunter (In Progress)"""
    logger.info("=" * 60)
    logger.info("PHASE 3: STOCK HUNTER (IN PROGRESS)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
    try:
        if not hasattr(stock_hunter_module, 'main'):
            logger.error("Stock hunter module not found")
            return 0
        
        result = stock_hunter_module.main()
        
        logger.info(f"Phase 3 stock hunter complete - result: {result}")
        return 1
    except Exception as e:
        logger.error(f"Phase 3 error: {str(e)}")
        return 0

def main():
    parser = argparse.ArgumentParser(description="Multi-Venue Runner - Shipping Mode")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow", help="Execution mode (micro-live = real trades with risk gates)")
    parser.add_argument("--phase", choices=["phase1", "phase2", "phase3", "all"], default="all", help="Phase to execute")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=10.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger.info("=" * 60)
    logger.info(f"MODE: {args.mode.upper()}")
    logger.info(f"PHASE: {args.phase.upper()}")
    logger.info(f"BANKROLL: ${args.bankroll:.2f}")
    logger.info(f"MAX POS: ${args.max_pos:.2f}")
    logger.info("=" * 60)
    
    results = {}
    
    if args.phase == "phase1" or args.phase == "all":
        logger.info("Starting Phase 1: Kalshi Optimization")
        result_phase1 = run_phase1_kalshi_optimization(
            mode=args.mode,
            bankroll=args.bankroll,
            max_pos_usd=args.max_pos
        )
        results["phase1"] = result_phase1
    else:
        results["phase1"] = 0
    
    if args.phase == "phase2" or args.phase == "all":
        logger.info("Starting Phase 2: SEF Spot Trading")
        result_phase2 = run_phase2_sef_spot_trading(
            mode=args.mode,
            bankroll=args.bankroll,
            max_pos_usd=args.max_pos
        )
        results["phase2"] = result_phase2
    else:
        results["phase2"] = 0
    
    if args.phase == "phase3" or args.phase == "all":
        logger.info("Starting Phase 3: Stock Hunter")
        result_phase3 = run_phase3_stock_hunter(
            mode=args.mode,
            bankroll=args.bankroll,
            max_pos_usd=args.max_pos
        )
        results["phase3"] = result_phase3
    else:
        results["phase3"] = 0
    
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Phase 1 (Kalshi): {'Success' if results['phase1'] else 'Failed'}")
    logger.info(f"Phase 2 (SEF): {'Success' if results['phase2'] else 'Failed'}")
    logger.info(f"Phase 3 (Stock Hunter): {'Success' if results['phase3'] else 'Failed'}")
    logger.info("=" * 60)
    
    proof_id = f"shipping_mode_{args.phase}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": args.mode,
        "phase": args.phase,
        "bankroll": args.bankroll,
        "max_pos_usd": args.max_pos,
        "results": results,
        "risk_caps": RISK_CAPS
    }
    
    generate_proof(proof_id, proof_data)
    logger.info(f"Proof: {proof_id}")
    
    exit_code = 0 if (results.get('phase1', 0) or results.get('phase2', 0) or results.get('phase3', 0)) else 1
    
    logger.info(f"Exit code: {exit_code}")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
