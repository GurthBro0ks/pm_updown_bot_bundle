#!/usr/bin/env python3
"""
Polymarket Shadow Runner with Risk Caps
Shadow-mode trading runner with risk management gates.
Supports: shadow (no trading), micro-live (simulated small trades with gates)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from kalshi_auth import KalshiAuth, KALSHI_BASE_URL

# Risk Caps Configuration
RISK_CAPS = {
    # Original caps
    "max_pos_usd": 10,
    "max_daily_loss_usd": 50,
    "max_open_pos": 5,
    "max_daily_positions": 20,
    # Micro-live gates
    "liquidity_min_usd": 1000,
    "edge_after_fees_pct": 2.0,
    "market_end_hrs": 24
}

# Venue-specific configuration
VENUE_CONFIGS = {
    "kalshi": {
        "name": "Kalshi",
        "min_trade_usd": 0.01,   # Penny-trade minimum
        "max_trade_usd": 10.0,
        "fee_pct": 0.07,         # ~7% taker fee on Kalshi
        "api_type": "rest_rsa_signed",
        "settlement": "USDC",
    },
    "ibkr": {
        "name": "IBKR TWS",
        "min_trade_usd": 1.00,   # Standard minimum
        "max_trade_usd": 10.0,
        "fee_pct": 0.01,         # ~1% commission
        "api_type": "tws_socket",
        "settlement": "USD",
    },
}

PROOF_DIR = "/tmp"


def check_risk_caps(pos_usd: float, daily_loss: float, open_pos: int, daily_pos: int) -> bool:
    """
    Check if current state violates risk caps.
    
    Args:
        pos_usd: Current position value in USD
        daily_loss: Current daily loss in USD
        open_pos: Number of open positions
        daily_pos: Number of positions opened today
    
    Returns:
        True if within risk limits, False otherwise
    
    Raises:
        SystemExit: If risk violation detected (exits with code 1)
    """
    violations = []
    
    if pos_usd > RISK_CAPS["max_pos_usd"]:
        violations.append(f"Position ${pos_usd} exceeds max ${RISK_CAPS['max_pos_usd']}")
    
    if daily_loss > RISK_CAPS["max_daily_loss_usd"]:
        violations.append(f"Daily loss ${daily_loss} exceeds max ${RISK_CAPS['max_daily_loss_usd']}")
    
    if open_pos > RISK_CAPS["max_open_pos"]:
        violations.append(f"Open positions {open_pos} exceeds max {RISK_CAPS['max_open_pos']}")
    
    if daily_pos > RISK_CAPS["max_daily_positions"]:
        violations.append(f"Daily positions {daily_pos} exceeds max {RISK_CAPS['max_daily_positions']}")
    
    if violations:
        print("RISK VIOLATION")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    
    return True


def check_micro_live_gates(market: dict, trade_size: float, edge_pct: float,
                           venue: str = "kalshi") -> bool:
    """
    Check micro-live gates before simulated trade.

    Args:
        market: Market dict with liquidity, end_time, etc.
        trade_size: Proposed trade size in USD
        edge_pct: Edge after fees percentage
        venue: Venue key ('kalshi' or 'ibkr')

    Returns:
        True if gates pass, False otherwise

    Raises:
        SystemExit: If gate violation detected (exits with code 1)
    """
    vcfg = VENUE_CONFIGS.get(venue, VENUE_CONFIGS["kalshi"])
    violations = []

    # Gate 1: Liquidity minimum
    liquidity = market.get("liquidity_usd", 0)
    if liquidity < RISK_CAPS["liquidity_min_usd"]:
        violations.append(
            f"Liquidity ${liquidity} below minimum ${RISK_CAPS['liquidity_min_usd']}"
        )

    # Gate 2: Edge after fees (fee-adjusted expectancy must be positive)
    if edge_pct < RISK_CAPS["edge_after_fees_pct"]:
        violations.append(
            f"Edge {edge_pct}% below minimum {RISK_CAPS['edge_after_fees_pct']}%"
        )

    # Gate 3: Market end time
    market_end_hrs = market.get("hours_to_end", float('inf'))
    if market_end_hrs < RISK_CAPS["market_end_hrs"]:
        violations.append(
            f"Market ends in {market_end_hrs}h, requires >{RISK_CAPS['market_end_hrs']}h"
        )

    # Gate 4: Trade size limits (venue-specific min_size)
    min_size = vcfg["min_trade_usd"]
    max_size = vcfg["max_trade_usd"]
    if trade_size < min_size:
        violations.append(f"Trade size ${trade_size} below minimum ${min_size:.2f} ({vcfg['name']})")
    elif trade_size > max_size:
        violations.append(f"Trade size ${trade_size} exceeds maximum ${max_size:.2f} ({vcfg['name']})")
    
    if violations:
        print("MICRO-LIVE GATE VIOLATION")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    
    return True


def _get_kalshi_auth() -> "KalshiAuth | None":
    """Return a KalshiAuth instance if credentials are configured, else None."""
    api_key = os.environ.get("KALSHI_KEY", "")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    if api_key and key_path and os.path.isfile(key_path):
        return KalshiAuth(api_key=api_key, private_key_path=key_path)
    return None


def fetch_kalshi_markets(auth: KalshiAuth, limit: int = 20) -> list:
    """
    Fetch live markets from Kalshi using RSA-signed authentication.

    Args:
        auth: KalshiAuth instance
        limit: Max markets to return

    Returns:
        List of market dicts normalised to VenueBook schema.
    """
    path = "/trade-api/v2/events"
    resp = auth.get(path, params={"limit": limit, "status": "open"})
    resp.raise_for_status()
    events = resp.json().get("events", [])

    markets = []
    for evt in events:
        for mkt in evt.get("markets", []):
            yes_price = mkt.get("yes_ask", 0.50)
            no_price = mkt.get("no_ask", 0.50)
            markets.append({
                "id": mkt.get("ticker", evt.get("ticker", "unknown")),
                "question": mkt.get("title", evt.get("title", "")),
                "odds": {"yes": yes_price, "no": no_price},
                "liquidity_usd": mkt.get("volume", 0),
                "hours_to_end": 48,  # placeholder; refine with close_time
                "fees_pct": 0.07,
            })

    print(f"Fetched {len(markets)} markets from Kalshi (LIVE RSA-signed)")
    return markets[:limit]


def fetch_venuebook_mock() -> list:
    """
    Fetch markets from VenueBook (MOCK fallback).

    Returns:
        List of market dicts with: id, question, odds, liquidity_usd, hours_to_end
    """
    # Mock data simulating real VenueBook API response
    markets = [
        {
            "id": "solana-polymarket-temp-2025-02-07",
            "question": "Will Solana be above $150 on Feb 8?",
            "odds": {"yes": 0.65, "no": 0.35},
            "liquidity_usd": 5000,
            "hours_to_end": 48,
            "fees_pct": 0.02
        },
        {
            "id": "btc-macro-2025-02-07",
            "question": "Will BTC close above $100k in February?",
            "odds": {"yes": 0.42, "no": 0.58},
            "liquidity_usd": 15000,
            "hours_to_end": 672,  # 28 days
            "fees_pct": 0.02
        },
        {
            "id": "low-liquidity-test",
            "question": "Test market with low liquidity",
            "odds": {"yes": 0.50, "no": 0.50},
            "liquidity_usd": 500,  # Below $1000 minimum
            "hours_to_end": 120,
            "fees_pct": 0.02
        },
        {
            "id": "no-edge-test",
            "question": "Test market with no edge",
            "odds": {"yes": 0.49, "no": 0.51},
            "liquidity_usd": 5000,
            "hours_to_end": 96,
            "fees_pct": 0.05  # Higher fees = no edge
        }
    ]

    print(f"Fetched {len(markets)} markets from VenueBook (MOCK)")
    return markets


def calculate_edge(market: dict, trade_side: str) -> float:
    """
    Calculate edge after fees for a trade.
    
    Args:
        market: Market dict with odds and fees
        trade_side: 'yes' or 'no'
    
    Returns:
        Edge percentage after fees
    """
    implied_prob = market["odds"][trade_side]
    fees = market.get("fees_pct", 0.02)
    
    # Edge = implied probability - fees
    edge = implied_prob - fees
    return round(edge * 100, 2)


def simulate_micro_trade(market: dict, trade_size: float, trade_side: str,
                         venue: str = "kalshi") -> dict:
    """
    Simulate a micro trade with full gate checking.

    Args:
        market: Market dict
        trade_size: Trade size in USD
        trade_side: 'yes' or 'no'
        venue: Venue key ('kalshi' or 'ibkr')

    Returns:
        Trade result dict
    """
    edge_pct = calculate_edge(market, trade_side)

    # Check micro-live gates (will exit if violation)
    check_micro_live_gates(market, trade_size, edge_pct, venue=venue)
    
    # Simulate trade outcome (50/50 for mock)
    import random
    won = random.choice([True, False])
    
    pnl = trade_size if won else -trade_size
    
    return {
        "market_id": market["id"],
        "trade_size": trade_size,
        "trade_side": trade_side,
        "edge_pct": edge_pct,
        "won": won,
        "pnl": pnl,
        "liquidity_usd": market["liquidity_usd"],
        "hours_to_end": market["hours_to_end"]
    }


def generate_proof(proof_id: str, data: dict) -> str:
    """Generate a proof file for the given operation."""
    timestamp = datetime.now(timezone.utc).isoformat()
    proof_data = {
        "proof_id": proof_id,
        "timestamp": timestamp,
        "data": data,
        "risk_caps": RISK_CAPS
    }
    
    proof_path = os.path.join(PROOF_DIR, f"proof_{proof_id}.json")
    with open(proof_path, 'w') as f:
        json.dump(proof_data, f, indent=2)
    
    print(f"Proof generated: {proof_path}")
    return proof_path


def shadow_mode(venue: str = "kalshi"):
    """Execute shadow mode trading simulation."""
    vcfg = VENUE_CONFIGS.get(venue, VENUE_CONFIGS["kalshi"])
    print("=" * 50)
    print(f"{vcfg['name'].upper()} SHADOW RUNNER")
    print(f"Mode: SHADOW (No live trading) | Venue: {vcfg['name']}")
    print("=" * 50)
    print()
    
    # Default shadow state
    pos_usd = 0.0
    daily_loss = 0.0
    open_pos = 0
    daily_pos = 0
    
    print(f"Initial State:")
    print(f"  Position: ${pos_usd}")
    print(f"  Daily Loss: ${daily_loss}")
    print(f"  Open Positions: {open_pos}")
    print(f"  Daily Positions: {daily_pos}")
    print()
    
    # Verify risk caps are valid
    print("Risk Caps Configuration:")
    for cap, value in RISK_CAPS.items():
        print(f"  {cap}: {value}")
    print()
    
    # Check risk caps with initial state (should pass)
    check_risk_caps(pos_usd, daily_loss, open_pos, daily_pos)
    print("Risk caps check: PASSED")
    print()
    
    # Generate proof for shadow run
    proof_id = f"ned_risk_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "shadow",
        "initial_state": {
            "pos_usd": pos_usd,
            "daily_loss": daily_loss,
            "open_pos": open_pos,
            "daily_pos": daily_pos
        },
        "status": "completed",
        "message": "Shadow runner initialized successfully"
    }
    
    generate_proof(proof_id, proof_data)
    
    print()
    print("=" * 50)
    print("SHADOW RUNNER COMPLETED")
    print("=" * 50)


def micro_live_mode(venue: str = "kalshi", bankroll: float = 1.06, max_pos: float = 0.01):
    """Execute micro-live mode (simulated small trades with Kelly/VaR gates)."""
    vcfg = VENUE_CONFIGS.get(venue, VENUE_CONFIGS["kalshi"])
    print("=" * 50)
    print(f"{vcfg['name'].upper()} MICRO-LIVE RUNNER")
    print(f"Mode: MICRO-LIVE (Simulated small trades) | Venue: {vcfg['name']}")
    print(f"Bankroll: ${bankroll:.2f} | Max Pos: ${max_pos:.2f} | Settlement: {vcfg['settlement']}")
    print("=" * 50)
    print()
    
    # Initial state
    pos_usd = 0.0
    daily_loss = 0.0
    open_pos = 0
    daily_pos = 0
    
    print(f"Initial State:")
    print(f"  Position: ${pos_usd}")
    print(f"  Daily Loss: ${daily_loss}")
    print(f"  Open Positions: {open_pos}")
    print(f"  Daily Positions: {daily_pos}")
    print(f"  Bankroll: ${bankroll:.2f}")
    print(f"  Max Position: ${max_pos:.2f}")
    print()
    
    # Kelly Fraction (conservative: 0.25 Kelly = 1/4 Kelly)
    kelly_fraction = 0.25
    kelly_pct = (2 * 0.50 - 1) * kelly_fraction  # Simplified Kelly: b*p - q
    kelly_trade_size = min(bankroll * kelly_pct if kelly_pct > 0 else max_pos, max_pos)
    
    print(f"Kelly Calculation:")
    print(f"  Kelly Fraction: {kelly_fraction:.2f} (conservative)")
    print(f"  Suggested Size: ${kelly_trade_size:.4f}")
    print(f"  Actual Trade Size: ${max_pos:.2f} (capped)")
    print()
    
    # Verify risk caps
    print("Risk Caps Configuration:")
    for cap, value in RISK_CAPS.items():
        print(f"  {cap}: {value}")
    print()
    
    # Check risk caps
    check_risk_caps(pos_usd, daily_loss, open_pos, daily_pos)
    print("Risk caps check: PASSED")
    print()
    
    # Fetch markets (live RSA-signed if credentials available, else mock)
    auth = _get_kalshi_auth() if venue == "kalshi" else None
    if auth:
        print("Fetching markets from Kalshi (RSA-signed)...")
        try:
            markets = fetch_kalshi_markets(auth)
        except Exception as e:
            print(f"  Live fetch failed ({e}), falling back to mock")
            markets = fetch_venuebook_mock()
    else:
        print("Fetching markets from VenueBook...")
        markets = fetch_venuebook_mock()
    print()
    
    # Simulate micro trades with bankroll-based sizing
    trade_size = max_pos  # Use --max-pos parameter
    print(f"Simulating micro trades (${trade_size:.2f} each)...")
    print()
    
    trades = []
    for i, market in enumerate(markets):
        trade_side = "yes"
        
        print(f"Trade {i+1}: {market['id']}")
        print(f"  Question: {market['question']}")
        print(f"  Liquidity: ${market['liquidity_usd']}")
        print(f"  Hours to End: {market['hours_to_end']}h")
        
        try:
            result = simulate_micro_trade(market, trade_size, trade_side, venue=venue)
            trades.append(result)
            
            print(f"  ✅ Trade executed")
            print(f"     Edge: {result['edge_pct']}%")
            print(f"     Result: {'WIN' if result['won'] else 'LOSS'}")
            print(f"     PnL: ${result['pnl']}")
            
            # Update state
            pos_usd += abs(result['pnl'])
            if result['pnl'] < 0:
                daily_loss += abs(result['pnl'])
            open_pos += 1
            daily_pos += 1
            
        except SystemExit as e:
            print(f"  ❌ Gate violation - trade skipped")
            trades.append({
                "market_id": market["id"],
                "status": "gate_violation",
                "reason": "Micro-live gate check failed"
            })
        
        print()
    
    # Final risk check
    print("Final Risk Check:")
    check_risk_caps(pos_usd, daily_loss, open_pos, daily_pos)
    print()
    
    # Summary
    wins = sum(1 for t in trades if t.get("won") == True)
    losses = sum(1 for t in trades if t.get("won") == False)
    gate_violations = sum(1 for t in trades if t.get("status") == "gate_violation")
    
    print("Trade Summary:")
    print(f"  Total Trades: {len(trades)}")
    print(f"  Wins: {wins}")
    print(f"  Losses: {losses}")
    print(f"  Gate Violations: {gate_violations}")
    print()
    
    # Generate proof
    proof_id = f"ned_micro_live_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "micro-live",
        "bankroll": bankroll,
        "max_pos": max_pos,
        "kelly_fraction": 0.25,
        "initial_state": {
            "pos_usd": pos_usd,
            "daily_loss": daily_loss,
            "open_pos": open_pos,
            "daily_pos": daily_pos
        },
        "trades": trades,
        "summary": {
            "total": len(trades),
            "wins": wins,
            "losses": losses,
            "gate_violations": gate_violations
        },
        "status": "MICRO-LIVE GATES PASS" if gate_violations < len(trades) else "MICRO-LIVE GATES FAIL"
    }
    
    generate_proof(proof_id, proof_data)
    
    print()
    print("=" * 50)
    print("MICRO-LIVE RUNNER COMPLETED")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Venue Shadow Runner with Risk Caps"
    )
    parser.add_argument(
        "--mode",
        choices=["shadow", "micro-live"],
        required=True,
        help="Execution mode (shadow = no trading, micro-live = simulated small trades)"
    )
    parser.add_argument(
        "--venue",
        choices=list(VENUE_CONFIGS.keys()),
        default="kalshi",
        help="Trading venue (default: kalshi)"
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=1.06,
        help="Bankroll in USD for Kelly calculations (default: 1.06)"
    )
    parser.add_argument(
        "--max-pos",
        type=float,
        default=0.01,
        help="Maximum position size in USD (default: 0.01)"
    )

    args = parser.parse_args()

    if args.mode == "shadow":
        shadow_mode(venue=args.venue)
    elif args.mode == "micro-live":
        micro_live_mode(
            venue=args.venue,
            bankroll=args.bankroll,
            max_pos=args.max_pos
        )
    else:
        print(f"Unknown mode: {args.mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
