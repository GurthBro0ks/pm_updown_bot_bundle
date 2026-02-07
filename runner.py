#!/usr/bin/env python3
"""
<<<<<<< HEAD
Polymarket/Kalshi Shadow Runner with Risk Caps
Multi-venue trading runner with risk management gates.
Supports: shadow (no trading), micro-live (simulated small trades with gates)
Venues: polymarket, kalshi
=======
Polymarket Shadow Runner with Risk Caps
Shadow-mode trading runner with risk management gates.
Supports: shadow (no trading), micro-live (simulated small trades with gates)
>>>>>>> main
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

<<<<<<< HEAD
# Risk Caps Configuration (shared across venues)
=======
# Risk Caps Configuration
>>>>>>> main
RISK_CAPS = {
    # Original caps
    "max_pos_usd": 10,
    "max_daily_loss_usd": 50,
    "max_open_pos": 5,
    "max_daily_positions": 20,
<<<<<<< HEAD
    # Micro-live gates (shared)
=======
    # Micro-live gates
>>>>>>> main
    "liquidity_min_usd": 1000,
    "edge_after_fees_pct": 2.0,
    "market_end_hrs": 24
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


<<<<<<< HEAD
def check_micro_live_gates(market: dict, trade_size: float, edge_pct: float, venue: str = "polymarket") -> bool:
=======
def check_micro_live_gates(market: dict, trade_size: float, edge_pct: float) -> bool:
>>>>>>> main
    """
    Check micro-live gates before simulated trade.
    
    Args:
        market: Market dict with liquidity, end_time, etc.
        trade_size: Proposed trade size in USD
        edge_pct: Edge after fees percentage
<<<<<<< HEAD
        venue: Venue name for gate customization
=======
>>>>>>> main
    
    Returns:
        True if gates pass, False otherwise
    
    Raises:
        SystemExit: If gate violation detected (exits with code 1)
    """
    violations = []
    
    # Gate 1: Liquidity minimum
    liquidity = market.get("liquidity_usd", 0)
    if liquidity < RISK_CAPS["liquidity_min_usd"]:
        violations.append(
<<<<<<< HEAD
            f"[{venue}] Liquidity ${liquidity} below minimum ${RISK_CAPS['liquidity_min_usd']}"
=======
            f"Liquidity ${liquidity} below minimum ${RISK_CAPS['liquidity_min_usd']}"
>>>>>>> main
        )
    
    # Gate 2: Edge after fees
    if edge_pct < RISK_CAPS["edge_after_fees_pct"]:
        violations.append(
<<<<<<< HEAD
            f"[{venue}] Edge {edge_pct}% below minimum {RISK_CAPS['edge_after_fees_pct']}%"
        )
    
    # Gate 3: Market end time (Kalshi-specific: US markets may have different hours)
    market_end_hrs = market.get("hours_to_end", float('inf'))
    if market_end_hrs < RISK_CAPS["market_end_hrs"]:
        violations.append(
            f"[{venue}] Market ends in {market_end_hrs}h, requires >{RISK_CAPS['market_end_hrs']}h"
=======
            f"Edge {edge_pct}% below minimum {RISK_CAPS['edge_after_fees_pct']}%"
        )
    
    # Gate 3: Market end time
    market_end_hrs = market.get("hours_to_end", float('inf'))
    if market_end_hrs < RISK_CAPS["market_end_hrs"]:
        violations.append(
            f"Market ends in {market_end_hrs}h, requires >{RISK_CAPS['market_end_hrs']}h"
>>>>>>> main
        )
    
    # Gate 4: Trade size limits
    if trade_size < 1.0:
<<<<<<< HEAD
        violations.append(f"[{venue}] Trade size ${trade_size} below minimum $1.00")
    elif trade_size > 10.0:
        violations.append(f"[{venue}] Trade size ${trade_size} exceeds maximum $10.00")
=======
        violations.append(f"Trade size ${trade_size} below minimum $1.00")
    elif trade_size > 10.0:
        violations.append(f"Trade size ${trade_size} exceeds maximum $10.00")
>>>>>>> main
    
    if violations:
        print("MICRO-LIVE GATE VIOLATION")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    
    return True


<<<<<<< HEAD
def fetch_polymarket_markets() -> list:
    """
    Fetch markets from Polymarket (MOCK).
=======
def fetch_venuebook_mock() -> list:
    """
    Fetch markets from VenueBook (MOCK for now).
>>>>>>> main
    
    Returns:
        List of market dicts with: id, question, odds, liquidity_usd, hours_to_end
    """
<<<<<<< HEAD
=======
    # Mock data simulating real VenueBook API response
>>>>>>> main
    markets = [
        {
            "id": "solana-polymarket-temp-2025-02-07",
            "question": "Will Solana be above $150 on Feb 8?",
            "odds": {"yes": 0.65, "no": 0.35},
            "liquidity_usd": 5000,
            "hours_to_end": 48,
<<<<<<< HEAD
            "fees_pct": 0.02,
            "venue": "polymarket"
=======
            "fees_pct": 0.02
>>>>>>> main
        },
        {
            "id": "btc-macro-2025-02-07",
            "question": "Will BTC close above $100k in February?",
            "odds": {"yes": 0.42, "no": 0.58},
            "liquidity_usd": 15000,
            "hours_to_end": 672,  # 28 days
<<<<<<< HEAD
            "fees_pct": 0.02,
            "venue": "polymarket"
=======
            "fees_pct": 0.02
>>>>>>> main
        },
        {
            "id": "low-liquidity-test",
            "question": "Test market with low liquidity",
            "odds": {"yes": 0.50, "no": 0.50},
            "liquidity_usd": 500,  # Below $1000 minimum
            "hours_to_end": 120,
<<<<<<< HEAD
            "fees_pct": 0.02,
            "venue": "polymarket"
=======
            "fees_pct": 0.02
>>>>>>> main
        },
        {
            "id": "no-edge-test",
            "question": "Test market with no edge",
            "odds": {"yes": 0.49, "no": 0.51},
            "liquidity_usd": 5000,
            "hours_to_end": 96,
<<<<<<< HEAD
            "fees_pct": 0.05,
            "venue": "polymarket"
        }
    ]
    
    print(f"Fetched {len(markets)} markets from Polymarket (MOCK)")
    return markets


def fetch_kalshi_markets() -> list:
    """
    Fetch markets from Kalshi REST API.
    
    Kalshi is US-legal, CFTC-regulated prediction market.
    API: https://api.kalshi.com/v1/markets
    
    Authentication: Basic Auth (base64 of key:secret)
    
    Environment:
        KALSHI_KEY: API key (short ID)
        KALSHI_SECRET: Secret key (long)
        KALSHI_BASE_URL: API base URL (default: https://api.kalshi.com)
    
    Returns:
        List of market dicts with: id, question, odds, liquidity_usd, hours_to_end
    """
    import base64
    import requests
    
    api_key = os.environ.get("KALSHI_KEY", "")
    api_secret = os.environ.get("KALSHI_SECRET", "")
    base_url = os.environ.get("KALSHI_BASE_URL", "https://api.kalshi.com")
    
    # Mock data for testing without credentials
    markets = [
        {
            "id": "kalshi-fed-rate-2025-03",
            "question": "Will Fed rate be unchanged in March 2025?",
            "odds": {"yes": 0.72, "no": 0.28},
            "liquidity_usd": 25000,
            "hours_to_end": 720,
            "fees_pct": 0.01,
            "venue": "kalshi",
            "category": "economics",
            "currency": "USD"
        },
        {
            "id": "kalshi-election-senate-2024",
            "question": "Will Democrats win Senate in 2024?",
            "odds": {"yes": 0.55, "no": 0.45},
            "liquidity_usd": 50000,
            "hours_to_end": 24,
            "fees_pct": 0.01,
            "venue": "kalshi",
            "category": "politics",
            "currency": "USD"
        },
        {
            "id": "kalshi-low-liquidity-test",
            "question": "Test market with low liquidity",
            "odds": {"yes": 0.50, "no": 0.50},
            "liquidity_usd": 500,
            "hours_to_end": 48,
            "fees_pct": 0.01,
            "venue": "kalshi",
            "category": "test",
            "currency": "USD"
        },
        {
            "id": "kalshi-ending-soon",
            "question": "Test market ending in 12h",
            "odds": {"yes": 0.60, "no": 0.40},
            "liquidity_usd": 10000,
            "hours_to_end": 12,
            "fees_pct": 0.01,
            "venue": "kalshi",
            "category": "test",
            "currency": "USD"
        }
    ]
    
    # If credentials provided, try real API fetch
    if api_key and api_secret:
        print(f"Fetching from Kalshi API: {base_url}")
        
        try:
            # Basic Auth: base64(key:secret)
            credentials = f"{api_key}:{api_secret}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers = {"Authorization": f"Basic {encoded}"}
            
            # Fetch markets endpoint
            url = f"{base_url}/v1/markets"
            params = {"status": "active", "limit": 20}
            
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                api_markets = data.get("markets", [])
                
                # Transform API response to our format
                for m in api_markets:
                    ticker = m.get("ticker", "")
                    markets.append({
                        "id": ticker,
                        "question": m.get("question", ticker),
                        "odds": {
                            "yes": m.get("yes_prob", 0.5),
                            "no": 1 - m.get("yes_prob", 0.5)
                        },
                        "liquidity_usd": m.get("liquidity", 0),
                        "hours_to_end": m.get("hours_to_end", 0),
                        "fees_pct": 0.01,  # Kalshi standard
                        "venue": "kalshi",
                        "category": m.get("category", "unknown"),
                        "currency": "USD",
                        "source": "api"
                    })
                
                print(f"Fetched {len(api_markets)} markets from Kalshi API")
            else:
                print(f"Kalshi API error: {response.status_code} - using mock data")
        except Exception as e:
            print(f"Kalshi API fetch failed ({e}) - using mock data")
    else:
        print("KALSHI_KEY/KALSHI_SECRET not set - using mock data")
    
    print(f"Total markets: {len(markets)} from Kalshi (MOCK)")
=======
            "fees_pct": 0.05  # Higher fees = no edge
        }
    ]
    
    print(f"Fetched {len(markets)} markets from VenueBook (MOCK)")
>>>>>>> main
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


<<<<<<< HEAD
def simulate_micro_trade(market: dict, trade_size: float, trade_side: str, venue: str) -> dict:
=======
def simulate_micro_trade(market: dict, trade_size: float, trade_side: str) -> dict:
>>>>>>> main
    """
    Simulate a micro trade with full gate checking.
    
    Args:
        market: Market dict
        trade_size: Trade size in USD
        trade_side: 'yes' or 'no'
<<<<<<< HEAD
        venue: Venue name
=======
>>>>>>> main
    
    Returns:
        Trade result dict
    """
    edge_pct = calculate_edge(market, trade_side)
    
    # Check micro-live gates (will exit if violation)
<<<<<<< HEAD
    check_micro_live_gates(market, trade_size, edge_pct, venue)
=======
    check_micro_live_gates(market, trade_size, edge_pct)
>>>>>>> main
    
    # Simulate trade outcome (50/50 for mock)
    import random
    won = random.choice([True, False])
    
    pnl = trade_size if won else -trade_size
    
    return {
        "market_id": market["id"],
<<<<<<< HEAD
        "venue": venue,
=======
>>>>>>> main
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
    timestamp = datetime.utcnow().isoformat() + "Z"
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


<<<<<<< HEAD
def shadow_mode(venue: str):
    """Execute shadow mode trading simulation."""
    print("=" * 50)
    print(f"{venue.upper()} SHADOW RUNNER")
=======
def shadow_mode():
    """Execute shadow mode trading simulation."""
    print("=" * 50)
    print("POLYMARKET SHADOW RUNNER")
>>>>>>> main
    print("Mode: SHADOW (No live trading)")
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
<<<<<<< HEAD
    proof_id = f"{venue}_shadow_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "shadow",
        "venue": venue,
=======
    proof_id = f"ned_risk_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "shadow",
>>>>>>> main
        "initial_state": {
            "pos_usd": pos_usd,
            "daily_loss": daily_loss,
            "open_pos": open_pos,
            "daily_pos": daily_pos
        },
        "status": "completed",
<<<<<<< HEAD
        "message": f"{venue} shadow runner initialized successfully"
=======
        "message": "Shadow runner initialized successfully"
>>>>>>> main
    }
    
    generate_proof(proof_id, proof_data)
    
    print()
    print("=" * 50)
<<<<<<< HEAD
    print(f"{venue.upper()} SHADOW RUNNER COMPLETED")
    print("=" * 50)


def micro_live_mode(venue: str):
    """Execute micro-live mode (simulated small trades with gates)."""
    print("=" * 50)
    print(f"{venue.upper()} MICRO-LIVE RUNNER")
=======
    print("SHADOW RUNNER COMPLETED")
    print("=" * 50)


def micro_live_mode():
    """Execute micro-live mode (simulated small trades with gates)."""
    print("=" * 50)
    print("POLYMARKET MICRO-LIVE RUNNER")
>>>>>>> main
    print("Mode: MICRO-LIVE (Simulated small trades)")
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
    
<<<<<<< HEAD
    # Fetch markets based on venue
    if venue == "polymarket":
        markets = fetch_polymarket_markets()
    elif venue == "kalshi":
        markets = fetch_kalshi_markets()
    else:
        print(f"Unknown venue: {venue}")
        sys.exit(1)
    
=======
    # Fetch markets from VenueBook
    print("Fetching markets from VenueBook...")
    markets = fetch_venuebook_mock()
>>>>>>> main
    print()
    
    # Simulate micro trades
    trade_size = 5.0  # $5 micro trade
    print(f"Simulating micro trades (${trade_size} each)...")
    print()
    
    trades = []
    for i, market in enumerate(markets):
        trade_side = "yes"
        
        print(f"Trade {i+1}: {market['id']}")
        print(f"  Question: {market['question']}")
<<<<<<< HEAD
        print(f"  Venue: {market.get('venue', venue)}")
=======
>>>>>>> main
        print(f"  Liquidity: ${market['liquidity_usd']}")
        print(f"  Hours to End: {market['hours_to_end']}h")
        
        try:
<<<<<<< HEAD
            result = simulate_micro_trade(market, trade_size, trade_side, venue)
=======
            result = simulate_micro_trade(market, trade_size, trade_side)
>>>>>>> main
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
<<<<<<< HEAD
                "venue": venue,
=======
>>>>>>> main
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
<<<<<<< HEAD
    proof_id = f"{venue}_micro_live_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "micro-live",
        "venue": venue,
=======
    proof_id = f"ned_micro_live_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "micro-live",
>>>>>>> main
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
<<<<<<< HEAD
    print(f"{venue.upper()} MICRO-LIVE RUNNER COMPLETED")
=======
    print("MICRO-LIVE RUNNER COMPLETED")
>>>>>>> main
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(
<<<<<<< HEAD
        description="Polymarket/Kalshi Shadow Runner with Risk Caps"
    )
    parser.add_argument(
        "--venue",
        choices=["polymarket", "kalshi"],
        default="polymarket",
        help="Trading venue (default: polymarket)"
=======
        description="Polymarket Shadow Runner with Risk Caps"
>>>>>>> main
    )
    parser.add_argument(
        "--mode",
        choices=["shadow", "micro-live"],
        required=True,
        help="Execution mode (shadow = no trading, micro-live = simulated small trades)"
    )
    
    args = parser.parse_args()
    
    if args.mode == "shadow":
<<<<<<< HEAD
        shadow_mode(args.venue)
    elif args.mode == "micro-live":
        micro_live_mode(args.venue)
=======
        shadow_mode()
    elif args.mode == "micro-live":
        micro_live_mode()
>>>>>>> main
    else:
        print(f"Unknown mode: {args.mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
