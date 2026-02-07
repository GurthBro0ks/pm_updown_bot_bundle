#!/usr/bin/env python3
"""
Polymarket Shadow Runner with Risk Caps
Shadow-mode trading runner with risk management gates.
Supports: shadow (no trading), micro-live (simulated small trades with gates)
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

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
        "api_type": "rest_basic_auth",
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
    "predictit": {
        "name": "PredictIt",
        "min_trade_usd": 1.00,
        "max_trade_usd": 850.0,  # $850 per-market cap
        "fee_pct": 0.10,         # 10% profit fee
        "api_type": "rest_public",
        "settlement": "USD",
        "mode": "sentiment_only",  # No live trades — sentiment aggregation only
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


def fetch_venuebook_mock() -> list:
    """
    Fetch markets from VenueBook (MOCK for now).
    
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


def kelly_fraction(edge_pct: float, odds: float) -> float:
    """
    Calculate Kelly Criterion optimal bet fraction.

    Formula: f = (p * b - (1 - p)) / b
      where p = win probability (edge-adjusted), b = decimal odds payout.

    Args:
        edge_pct: Edge after fees as a percentage (e.g. 58.0 means 58%).
        odds: Decimal odds (payout ratio, e.g. 1.86 for 65% implied prob).

    Returns:
        Kelly fraction in [0, 1]. Clamped to 0 if negative (no bet).
    """
    p = edge_pct / 100.0
    if p <= 0 or odds <= 0:
        return 0.0
    b = odds
    f = (p * b - (1.0 - p)) / b
    return max(0.0, min(1.0, round(f, 6)))


def monte_carlo_var(bankroll: float, edge_pct: float, trade_size: float,
                    n_trades: int = 20, n_sims: int = 1000,
                    confidence: float = 0.95) -> dict:
    """
    Estimate Value-at-Risk via Monte Carlo simulation.

    Simulates n_sims paths of n_trades each. Each trade wins with probability
    derived from edge_pct. Returns the VaR at the given confidence level
    (worst-case loss at the percentile boundary).

    Args:
        bankroll: Starting capital in USD.
        edge_pct: Win probability as percentage (e.g. 58.0).
        trade_size: Fixed notional per trade in USD.
        n_trades: Number of trades per simulation path.
        n_sims: Number of Monte Carlo paths.
        confidence: Confidence level (e.g. 0.95 for 95% VaR).

    Returns:
        Dict with var_usd, var_pct, mean_pnl, max_drawdown, sim_paths count.
    """
    win_prob = edge_pct / 100.0
    pnls = []
    max_dds = []

    for _ in range(n_sims):
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for _ in range(n_trades):
            if random.random() < win_prob:
                cumulative += trade_size
            else:
                cumulative -= trade_size
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        pnls.append(cumulative)
        max_dds.append(max_dd)

    pnls.sort()
    var_index = int((1.0 - confidence) * n_sims)
    var_usd = abs(pnls[var_index]) if pnls[var_index] < 0 else 0.0

    return {
        "var_usd": round(var_usd, 4),
        "var_pct": round((var_usd / bankroll) * 100, 2) if bankroll > 0 else 0.0,
        "mean_pnl": round(sum(pnls) / len(pnls), 4),
        "max_drawdown": round(max(max_dds), 4),
        "sim_paths": n_sims,
        "n_trades": n_trades,
        "confidence": confidence,
    }


def optimal_position_size(bankroll: float, edge_pct: float, odds: float,
                          max_daily_loss: float, venue: str = "kalshi") -> dict:
    """
    Compute position size as min(Kelly-sized, VaR-constrained).

    Args:
        bankroll: Current bankroll in USD.
        edge_pct: Edge after fees as percentage.
        odds: Decimal odds payout.
        max_daily_loss: Maximum daily loss allowed in USD.
        venue: Venue key for min/max bounds.

    Returns:
        Dict with kelly_frac, kelly_size, var_limit, final_size, method.
    """
    vcfg = VENUE_CONFIGS.get(venue, VENUE_CONFIGS["kalshi"])
    kf = kelly_fraction(edge_pct, odds)
    kelly_size = round(bankroll * kf, 4)

    # VaR constraint: size should not exceed what would breach daily loss limit
    var_result = monte_carlo_var(bankroll, edge_pct, max(kelly_size, vcfg["min_trade_usd"]))
    # If VaR exceeds daily loss limit, scale down
    if var_result["var_usd"] > 0 and var_result["var_usd"] > max_daily_loss:
        scale = max_daily_loss / var_result["var_usd"]
        var_limit = round(kelly_size * scale, 4)
    else:
        var_limit = kelly_size

    # Clamp to venue bounds
    final_size = max(vcfg["min_trade_usd"], min(var_limit, vcfg["max_trade_usd"]))
    final_size = round(final_size, 4)

    method = "kelly" if final_size >= kelly_size else "var_constrained"
    if final_size == vcfg["min_trade_usd"] and kelly_size < vcfg["min_trade_usd"]:
        method = "venue_minimum"

    return {
        "kelly_frac": kf,
        "kelly_size": kelly_size,
        "var_limit": round(var_limit, 4),
        "var_usd": var_result["var_usd"],
        "final_size": final_size,
        "method": method,
        "venue": venue,
    }


def scan_venues(markets: list, venues: list = None) -> list:
    """
    Scan multiple venues and aggregate weighted edge across markets.

    For each market, computes edge at each venue (adjusting for venue fees).
    PredictIt is sentiment-only: its odds inform edge but no trades are placed.

    Args:
        markets: List of market dicts.
        venues: List of venue keys to scan (default: all configured).

    Returns:
        List of dicts with market_id, best_venue, weighted_edge, venue_edges.
    """
    if venues is None:
        venues = list(VENUE_CONFIGS.keys())

    results = []
    for market in markets:
        venue_edges = {}
        weights = {"kalshi": 0.5, "ibkr": 0.3, "predictit": 0.2}

        for v in venues:
            vcfg = VENUE_CONFIGS.get(v)
            if not vcfg:
                continue
            # Implied prob from market odds (best side)
            best_side = max(market["odds"], key=market["odds"].get)
            implied_prob = market["odds"][best_side]
            fee = vcfg["fee_pct"]
            edge = round((implied_prob - fee) * 100, 2)
            venue_edges[v] = {
                "edge_pct": edge,
                "fee_pct": fee,
                "tradeable": vcfg.get("mode") != "sentiment_only",
                "liquidity_ok": market.get("liquidity_usd", 0) >= RISK_CAPS["liquidity_min_usd"],
            }

        # Weighted average edge across venues
        total_weight = 0.0
        weighted_sum = 0.0
        for v, info in venue_edges.items():
            w = weights.get(v, 0.1)
            weighted_sum += info["edge_pct"] * w
            total_weight += w
        weighted_edge = round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0

        # Best tradeable venue (highest edge among tradeable venues)
        tradeable = {v: info for v, info in venue_edges.items() if info["tradeable"] and info["liquidity_ok"]}
        best_venue = max(tradeable, key=lambda v: tradeable[v]["edge_pct"]) if tradeable else None

        results.append({
            "market_id": market["id"],
            "best_venue": best_venue,
            "weighted_edge": weighted_edge,
            "venue_edges": venue_edges,
        })

    return results


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


def micro_live_mode(venue: str = "kalshi", bankroll: float = 1.06):
    """Execute micro-live mode (simulated small trades with Kelly/VaR sizing)."""
    vcfg = VENUE_CONFIGS.get(venue, VENUE_CONFIGS["kalshi"])
    print("=" * 50)
    print(f"{vcfg['name'].upper()} MICRO-LIVE RUNNER (Kelly/VaR)")
    print(f"Mode: MICRO-LIVE (Simulated small trades) | Venue: {vcfg['name']}")
    print(f"Min trade: ${vcfg['min_trade_usd']:.2f} | Settlement: {vcfg['settlement']}")
    print(f"Bankroll: ${bankroll:.2f} | Kelly+VaR sizing active")
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

    # Fetch markets from VenueBook
    print("Fetching markets from VenueBook...")
    markets = fetch_venuebook_mock()
    print()

    # Multi-venue scan
    print("Scanning venues for best edge...")
    scan_results = scan_venues(markets)
    for sr in scan_results:
        print(f"  {sr['market_id']}: weighted_edge={sr['weighted_edge']}% best_venue={sr['best_venue']}")
    print()

    trades = []
    current_bankroll = bankroll

    for i, market in enumerate(markets):
        trade_side = "yes"
        edge_pct = calculate_edge(market, trade_side)

        # Compute decimal odds from implied probability
        implied_prob = market["odds"][trade_side]
        odds = round(1.0 / implied_prob, 4) if implied_prob > 0 else 0.0

        # Kelly/VaR optimal sizing
        sizing = optimal_position_size(
            current_bankroll, edge_pct, odds,
            RISK_CAPS["max_daily_loss_usd"], venue=venue
        )
        trade_size = sizing["final_size"]

        print(f"Trade {i+1}: {market['id']}")
        print(f"  Question: {market['question']}")
        print(f"  Liquidity: ${market['liquidity_usd']}")
        print(f"  Hours to End: {market['hours_to_end']}h")
        print(f"  Kelly: f={sizing['kelly_frac']:.4f} size=${sizing['kelly_size']:.4f}")
        print(f"  VaR limit: ${sizing['var_limit']:.4f} | Method: {sizing['method']}")
        print(f"  Trade size: ${trade_size:.4f}")

        try:
            result = simulate_micro_trade(market, trade_size, trade_side, venue=venue)
            result["sizing"] = sizing
            trades.append(result)

            print(f"  Trade executed")
            print(f"     Edge: {result['edge_pct']}%")
            print(f"     Result: {'WIN' if result['won'] else 'LOSS'}")
            print(f"     PnL: ${result['pnl']:.4f}")

            # Update state
            current_bankroll += result['pnl']
            pos_usd += abs(result['pnl'])
            if result['pnl'] < 0:
                daily_loss += abs(result['pnl'])
            open_pos += 1
            daily_pos += 1

        except SystemExit:
            print(f"  Gate violation - trade skipped")
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
    wins = sum(1 for t in trades if t.get("won") is True)
    losses = sum(1 for t in trades if t.get("won") is False)
    gate_violations = sum(1 for t in trades if t.get("status") == "gate_violation")
    total_pnl = sum(t.get("pnl", 0) for t in trades if "pnl" in t)
    roi_pct = round((total_pnl / bankroll) * 100, 2) if bankroll > 0 else 0.0

    print("Trade Summary:")
    print(f"  Total Trades: {len(trades)}")
    print(f"  Wins: {wins}")
    print(f"  Losses: {losses}")
    print(f"  Gate Violations: {gate_violations}")
    print(f"  Starting Bankroll: ${bankroll:.2f}")
    print(f"  Final Bankroll: ${current_bankroll:.4f}")
    print(f"  Total PnL: ${total_pnl:.4f}")
    print(f"  ROI: {roi_pct}%")
    print()

    # Generate proof
    proof_id = f"ned_micro_live_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "micro-live",
        "bankroll_start": bankroll,
        "bankroll_end": round(current_bankroll, 4),
        "roi_pct": roi_pct,
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
            "gate_violations": gate_violations,
            "total_pnl": round(total_pnl, 4),
            "roi_pct": roi_pct,
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
        choices=["kalshi", "ibkr"],
        default="kalshi",
        help="Trading venue (default: kalshi). PredictIt is sentiment-only."
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=1.06,
        help="Starting bankroll in USD (default: 1.06 USDC)"
    )

    args = parser.parse_args()

    if args.mode == "shadow":
        shadow_mode(venue=args.venue)
    elif args.mode == "micro-live":
        micro_live_mode(venue=args.venue, bankroll=args.bankroll)
    else:
        print(f"Unknown mode: {args.mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
