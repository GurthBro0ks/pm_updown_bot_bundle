#!/usr/bin/env python3
"""
Quick demo script for wallet tokens + DEX arbitrage features.
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.wallet_tokens import WALLET_ADDRESS, get_holdings_summary
from utils.dex_prices import (
    get_token_price_by_symbol,
    get_cex_price,
    check_arbitrage_opportunity,
    scan_for_arbitrage,
    POPULAR_TOKENS
)

def demo_wallet_tokens():
    """Demo wallet token discovery."""
    print("\n" + "="*60)
    print("WALLET TOKEN DISCOVERY")
    print("="*60)
    print(f"Wallet Address: {WALLET_ADDRESS}\n")

    summary = get_holdings_summary()
    print(f"Total Value: ${summary.get('total_usd', 0):.2f}")
    print(f"Chains scanned: {len(summary.get('chains', {}))}")

    for chain, data in summary.get("chains", {}).items():
        print(f"\n{chain} (${data.get('total_usd', 0):.2f})")
        for token in data.get('tokens', [])[:3]:  # Show first 3 tokens
            print(f"  - {token['symbol']}: {token['balance']:.6f} (${token.get('balance_usd', 0):.2f})")
        if len(data['tokens']) > 3:
            print(f"  ... and {len(data['tokens']) - 3} more tokens")


def demo_dex_prices():
    """Demo DEX price fetching."""
    print("\n" + "="*60)
    print("DEX PRICES (DexScreener)")
    print("="*60)

    test_tokens = ["ETH", "WBTC", "USDC", "LINK", "UNI"]
    for symbol in test_tokens:
        price = get_token_price_by_symbol(symbol)
        if price:
            print(f"{symbol}: ${price:.4f}")
        else:
            print(f"{symbol}: Not found")


def demo_cex_prices():
    """Demo CEX price fetching."""
    print("\n" + "="*60)
    print("CEX PRICES (Coinbase/Binance)")
    print("="*60)

    test_tokens = ["ETH", "BTC", "LINK", "UNI", "AAVE"]
    for symbol in test_tokens:
        price = get_cex_price(symbol)
        if price:
            print(f"{symbol}: ${price:.4f}")
        else:
            print(f"{symbol}: Not found")


def demo_arbitrage():
    """Demo arbitrage opportunity detection."""
    print("\n" + "="*60)
    print("ARBITRAGE OPPORTUNITIES (Min Spread: 1%)")
    print("="*60)

    # scan_for_arbitrage takes (tokens, min_spread) as positional args
    opportunities = scan_for_arbitrage(POPULAR_TOKENS[:10], 1.0)

    if opportunities:
        for opp in opportunities[:5]:  # Show top 5
            print(f"\n{opp.token}")
            print(f"  DEX:  ${opp.dex_price:.4f}")
            print(f"  CEX:  ${opp.cex_price:.4f}")
            print(f"  Spread: {opp.spread_percent:.2f}%")
            print(f"  Profit (after $5 gas): ${opp.profit_margin:.2f}")
    else:
        print("No arbitrage opportunities found.")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("WALLET TOKENS + DEX ARBITRAGE DEMO")
    print("="*60)

    # Run all demos
    demo_wallet_tokens()
    demo_dex_prices()
    demo_cex_prices()
    demo_arbitrage()

    print("\n" + "="*60)
    print("DEMO COMPLETE")
    print("="*60)
    print("\nUsage in runner.py:")
    print("  from runner import check_wallet_holdings, check_arbitrage_for_holdings, scan_popular_arbitrage")
    print("  summary = check_wallet_holdings()")
    print("  opps = check_arbitrage_for_holdings(min_spread=2.0)")
    print("  popular_opps = scan_popular_arbitrage(min_spread=2.0)")
    print()
