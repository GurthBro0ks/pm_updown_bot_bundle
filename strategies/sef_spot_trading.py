#!/usr/bin/env python3
"""
SEF Spot Trading Module - Phase 2
Uniswap V3 only (dYdX/GMX REMOVED - illegal derivatives for US residents)

Configuration centralized in config.py
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

# CCXT for multi-exchange fetching (LEGAL - spot only, no derivatives)
import ccxt

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import centralized config
from config import RISK_CAPS, SEF_TRADING_PAIRS

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

# SEF Configuration - LEGAL ONLY (no derivatives)
# dYdX v4 and GMX V2 REMOVED - unregistered derivatives, CFTC violation risk
# Only spot DEX/CEX comparisons are legal for US retail

SEF_CONFIGS = {
    "uniswap_v3": {
        "name": "Uniswap V3",
        "chain": "ethereum",
        "rpc_url": os.getenv("UNISWAP_RPC_URL", "https://mainnet.infura.io/v3/YOUR_KEY"),
        "router_address": "0xE592427A0AEce92De3Edee1F18E0157C05861562",
        "min_trade_usd": 0.01,
        "slippage_pct": 0.5
    },
    # dYdX v4: REMOVED - unregistered perps, CFTC violation risk for US retail
    # GMX V2: REMOVED - unregistered perps, CFTC violation risk for US retail
    # TODO: Add 0x API for aggregated DEX spot prices (Priority 1B)
    # TODO: Add Coinbase/Kraken CEX feeds for arb comparison (Priority 1C)
}

# CCXT Configuration - LEGAL spot exchanges only (no derivatives)
# dYdX/GMX REMOVED - illegal unregistered derivatives
CCXT_EXCHANGES = {
    "kraken": ccxt.kraken(),
    "coinbase": ccxt.coinbase(),
    "binance": ccxt.binance(),
}

# Trading pairs to monitor
PAIRS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD",
    "DOGE/USD", "AVAX/USD", "DOT/USD", "LINK/USD", "POL/USD",
    "UNI/USD", "ATOM/USD", "LTC/USD", "NEAR/USD", "FIL/USD",
    "APT/USD", "ARB/USD", "OP/USD", "SUI/USD", "AAVE/USD",
]

# Fee-aware minimum spread threshold
# Kraken maker: 0.16%, taker: 0.26%
# Coinbase Advanced: maker 0.40%, taker 0.60%
# Worst case: buy Coinbase (0.60%) + sell Kraken (0.26%) = 0.86%
# Minimum edge needed: ~1.0% to be profitable after fees + slippage buffer
CRYPTO_MIN_EDGE = 0.010  # 1.0% minimum spread

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


# 0x API - Aggregated DEX spot prices (LEGAL for CEX comparison)
# dYdX/GMX removed - unregistered derivatives, illegal for US residents
API_URL = "https://api.0x.org"

def get_0x_spot_price(token_pair):
    """Get spot price from 0x API - free, no auth needed"""
    try:
        url = f"{API_URL}/s/spot/price"
        params = {
            "tokens": token_pair,  # e.g., "WBTC-USDC"
            "side": "sell",
            "chain": "ethereum"
        }
        headers = {"accept": "application/json"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data and data.get("price"):
            return float(data["price"])
        return None
    except Exception as e:
        logger.error(f"0x API error for {token_pair}: {e}")
        return None


# CEX price feeds (Coinbase/Kraken) - FREE via REST API
COINBASE_API = "https://api.coinbase.com/v2"
KRAKEN_API = "https://api.kraken.com/0/public"

def get_coinbase_price(pair):
    """Get Coinbase spot price (CEX)"""
    try:
        # Convert WBTC-USDC to BTC-USD format
        if "WBTC" in pair:
            pair = pair.replace("WBTC", "BTC")
        if "-USDC" in pair or "-USDT" in pair:
            pair = pair.replace("-USDC", "-USD").replace("-USDT", "-USD")
        
        url = f"{COINBASE_API}/prices/{pair}/spot"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data and "data" in data and "amount" in data["data"]:
            return float(data["data"]["amount"])
        return None
    except Exception as e:
        logger.error(f"Coinbase API error for {pair}: {e}")
        return None


def get_kraken_price(pair):
    """Get Kraken spot price (CEX)"""
    try:
        # Convert to Kraken format
        kraken_pair = pair.replace("-", "")
        if "WBTC" in kraken_pair:
            kraken_pair = kraken_pair.replace("WBTC", "XBT")
        
        url = f"{KRAKEN_API}/Ticker"
        params = {"pair": kraken_pair}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data and "result" in data:
            # Get first available price (ask price)
            result = list(data["result"].values())[0]
            if "a" in result:
                return float(result["a"][0])  # Ask price
        return None
    except Exception as e:
        logger.error(f"Kraken API error for {pair}: {e}")
        return None


def fetch_ccxt_prices():
    """Fetch spot prices from ccxt-supported exchanges (LEGAL - spot only)."""
    prices = {}
    for name, exchange in CCXT_EXCHANGES.items():
        try:
            tickers = exchange.fetch_tickers(PAIRS)
            for pair, data in tickers.items():
                last_price = data.get("last")
                if last_price:
                    # Use bid/ask if available, otherwise use last as both (mid-price)
                    bid = data.get("bid") or last_price
                    ask = data.get("ask") or last_price
                    prices.setdefault(pair, {})[name] = {
                        "bid": bid,
                        "ask": ask,
                        "last": last_price,
                        "volume": data.get("quoteVolume", 0),
                    }
            logger.info(f"[CCXT] {name}: fetched {len(tickers)} tickers")
        except Exception as e:
            logger.warning(f"[CCXT] {name} fetch failed: {e}")
    return prices


def find_crypto_arb_opportunities(prices: dict, min_spread_pct: float = 0.010):
    """Find cross-exchange arbitrage opportunities (LEGAL spot trading)."""
    opportunities = []
    for pair, exchange_prices in prices.items():
        if len(exchange_prices) < 2:
            continue

        # Get all prices
        for ex_a, a_data in exchange_prices.items():
            for ex_b, b_data in exchange_prices.items():
                if ex_a >= ex_b:
                    continue

                if not all([a_data.get("ask"), b_data.get("bid")]):
                    continue

                # Buy on A (ask), sell on B (bid)
                spread = (b_data["bid"] - a_data["ask"]) / a_data["ask"] * 100

                # Detailed spread logging
                direction = "BUY_KRAKEN" if ex_a == "kraken" else "BUY_COINBASE"
                logger.info(f"[CRYPTO] {pair}: {ex_a}={a_data['ask']:.4f} {ex_b}={b_data['bid']:.4f} spread={spread:.4f}% {direction}")
                # min_spread_pct is in decimal (0.010 = 1.0%), convert to percentage for comparison
                if spread > min_spread_pct * 100:
                    logger.info(f"[CRYPTO ARB] *** {pair} {direction} spread={spread:.2f}% > {min_spread_pct*100:.2f}% ***")
                    opportunities.append({
                        "pair": pair,
                        "buy_exchange": ex_a,
                        "sell_exchange": ex_b,
                        "spread_pct": spread,
                        "buy_price": a_data["ask"],
                        "sell_price": b_data["bid"],
                    })

    return sorted(opportunities, key=lambda x: x["spread_pct"], reverse=True)


# CoinGecko fallback - FREE public API (LEGAL - spot prices only)
def fetch_coingecko_prices():
    """Fallback price source using CoinGecko API."""
    prices = {}
    try:
        # Map symbols to CoinGecko IDs
        coingecko_ids = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
            "XRP": "ripple",
            "ADA": "cardano",
        }

        # Use free public endpoint (no API key needed for basic prices)
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": ",".join(coingecko_ids.values()),
            "vs_currencies": "usd",
        }
        headers = {"accept": "application/json"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for symbol, gecko_id in coingecko_ids.items():
            if gecko_id in data and "usd" in data[gecko_id]:
                prices[f"{symbol}/USD"] = {
                    "gecko": {
                        "last": data[gecko_id]["usd"],
                    }
                }

        logger.info(f"[CoinGecko] Fetched {len(prices)} prices")
    except Exception as e:
        logger.warning(f"[CoinGecko] Fetch failed: {e}")

    return prices


# dYdX v4 and GMX V2 REMOVED - unregistered perps, CFTC violation risk
# These functions replaced with legal alternatives above


def find_sef_arbitrage_opportunity(token_pairs):
    """Find SEF arbitrage opportunity - compares 0x DEX vs CEX prices"""
    opportunities = []
    
    for pair in token_pairs:
        # Get DEX price from 0x
        dex_price = get_0x_spot_price(pair)
        
        # Get CEX prices from Coinbase/Kraken
        cex_price_coinbase = get_coinbase_price(pair)
        cex_price_kraken = get_kraken_price(pair)
        
        if not dex_price:
            continue
        
        # Use best CEX price
        cex_prices = []
        if cex_price_coinbase:
            cex_prices.append(("coinbase", cex_price_coinbase))
        if cex_price_kraken:
            cex_prices.append(("kraken", cex_price_kraken))
        
        if not cex_prices:
            continue
        
        # Find best CEX price for selling
        best_cex = min(cex_prices, key=lambda x: x[1])
        cex_name, cex_price = best_cex
        
        # Calculate spread (CEX vs DEX)
        spread = abs(cex_price - dex_price)
        spread_pct = (spread / cex_price) * 100 if cex_price > 0 else 0
        
        # Only flag if spread > 0.50% (combined fee threshold)
        # DEX ~0.3% gas + CEX ~0.2% = ~0.5% total cost
        if spread_pct > 0.50:
            net_spread_pct = spread_pct - 0.50  # Net after fees
            opportunities.append({
                "token_pair": pair,
                "dex_price": dex_price,
                "cex_price": cex_price,
                "cex_venue": cex_name,
                "spread_pct": spread_pct,
                "net_spread_pct": net_spread_pct,
                "profitable": net_spread_pct > 0,
                "estimated_gas_usd": 15.0  # Arbitrum gas
            })
            logger.info(f"Arb opportunity: {pair}")
            logger.info(f"  DEX: ${dex_price:.2f} | CEX ({cex_name}): ${cex_price:.2f}")
            logger.info(f"  Spread: {spread_pct:.2f}% | Net after fees: {net_spread_pct:.2f}%")
        else:
            logger.debug(f"{pair}: spread {spread_pct:.2f}% < 0.50% threshold")
    
    logger.info(f"Found {len(opportunities)} arbitrage opportunities (CEX vs DEX)")
    return opportunities

def find_sef_arbitrage_opportunity(token_in, token_out):
    """
    Find arbitrage opportunity across SEFs (Uniswap V3 only - dYdX/GMX removed)
    Only for spot trading (no derivatives)
    """
    prices = {}
    
    # Get prices from all SEFs (dYdX/GMX removed - illegal derivatives)
    prices["uniswap"] = get_uniswap_price(token_in, token_out, 1.0)

    # Find best price
    best_exchange = min(prices, key=prices.get)
    best_price = prices[best_exchange]
    worst_exchange = max(prices, key=prices.get)
    worst_price = prices[worst_exchange]
    
    # Calculate arbitrage
    price_spread_pct = ((best_price - worst_price) / worst_price) * 100
    gross_spread = best_price - worst_price
    
    # Estimate gas costs (Ethereum mainnet)
    gas_costs_eth = 10.0

    # Calculate net profit after gas (dYdX/GMX removed - illegal derivatives)
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
            "chain": "ethereum"  # dYdX/GMX removed
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
    from utils.proof import generate_proof
    generate_proof(proof_id, proof_data)
    
    logger.info(f"Proof: {proof_id}")
    
    return len(orders)

def main(mode="shadow", bankroll=100.0, max_pos_usd=20.0, verbose=False):
    """Phase 2: Crypto Spot Trading - MONITOR MODE (no auto-trade)"""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("PHASE 2: CRYPTO SPOT TRADING (MONITOR MODE)")
    logger.info("Legal venues: Kraken, Coinbase, Binance (spot only)")
    logger.info("dYdX/GMX/Hyperliquid REMOVED - illegal derivatives")
    logger.info("=" * 60)

    # Log legal compliance status
    logger.info("[SEF] REMOVED dYdX, GMX, Hyperliquid (legal compliance - CFTC unregistered derivatives)")
    logger.info("[SEF] Active venues: Kraken, Coinbase, Binance, 0x API, Uniswap V3")

    # Fetch prices from multiple exchanges using ccxt
    prices = fetch_ccxt_prices()
    logger.info(f"[CRYPTO] Prices fetched from {len(prices)} pairs")

    # Find arbitrage opportunities
    opps = find_crypto_arb_opportunities(prices, min_spread_pct=CRYPTO_MIN_EDGE)

    if opps:
        for opp in opps[:5]:  # Top 5
            logger.info(f"[CRYPTO ARB] {opp['pair']}: {opp['spread_pct']:.2f}% "
                       f"Buy {opp['buy_exchange']}@{opp['buy_price']:.2f} -> "
                       f"Sell {opp['sell_exchange']}@{opp['sell_price']:.2f}")
    else:
        logger.info(f"[CRYPTO] No arb opportunities > {CRYPTO_MIN_EDGE*100}%")

    # Also try CoinGecko fallback if no ccxt prices
    if not prices:
        logger.info("[CRYPTO] Trying CoinGecko fallback...")
        gecko_prices = fetch_coingecko_prices()
        if gecko_prices:
            logger.info(f"[CRYPTO] CoinGecko fallback: {len(gecko_prices)} prices")

    # Generate proof
    proof_id = f"crypto_spot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": mode,
        "prices": prices,
        "opportunities": opps,
        "legal_venues": ["kraken", "coinbase", "binance"],
        "removed_venues": ["dydx", "gmx", "hyperliquid"],
    }

    # Import generate_proof from runner module
    try:
        from utils.proof import generate_proof
        generate_proof(proof_id, proof_data)
        logger.info(f"Proof: {proof_id}")
    except Exception as e:
        logger.warning(f"Proof generation skipped: {e}")

    logger.info(f"Exit code: {len(opps)}")
    return len(opps)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEF Spot Trading - Phase 2")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow", help="Execution mode")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=20.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    sys.exit(main(mode=args.mode, bankroll=args.bankroll, max_pos_usd=args.max_pos, verbose=args.verbose))
