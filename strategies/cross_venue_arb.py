#!/usr/bin/env python3
"""Cross-Venue Arbitrage Strategy Tuned for 3%+ spreads with proper risk management"""

from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

# ARB-SPECIFIC GATES (More aggressive than single-venue)
ARB_CONFIG = {
    "min_spread_pct": 0.025,  # 2.5% minimum spread (was 3%)
    "min_net_edge_pct": 0.015,  # 1.5% after estimated fees
    "min_trade_usd": 0.005,  # $0.005 minimum (lower for arb)
    "max_trade_usd": 100,  # $100 max per leg
    "fee_estimate_pct": 0.01,  # Assume 1% total fees
}

def calculate_arb_edge(venue_a_price: float, venue_b_price: float, venue_a_fees: float = 0.005, venue_b_fees: float = 0.005) -> Dict:
    """ Calculate true arbitrage edge after fees """
    gross_spread = abs(venue_a_price - venue_b_price)
    total_fees = venue_a_fees + venue_b_fees
    net_edge = gross_spread - total_fees
    net_edge_pct = net_edge / (venue_a_price + venue_b_price) * 2  # Normalized return
    return {
        "gross_spread": gross_spread,
        "gross_spread_pct": (gross_spread / venue_a_price) if venue_a_price > 0 else 0,
        "total_fees": total_fees,
        "net_edge": net_edge,
        "net_edge_pct": net_edge_pct,
        "profitable": net_edge > 0 and net_edge_pct >= ARB_CONFIG["min_net_edge_pct"]
    }

def market_similarity(text1: str, text2: str) -> float:
    """Simple word overlap similarity"""
    words1 = set(text1.split())
    words2 = set(text2.split())
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union) if union else 0.0

def determine_strategy(kalshi_price: float, poly_price: float) -> str:
    """Determine which side to take on each venue"""
    if kalshi_price < poly_price:
        return "BUY_KALSHI_YES / SELL_POLYMARKET_YES"
    else:
        return "SELL_KALSHI_YES / BUY_POLYMARKET_YES"

def find_arbitrage_opportunities(venues: Dict[str, any], markets_kalshi: List = None, markets_polymarket: List = None) -> List[Dict]:
    """ Find cross-venue arbitrage opportunities
    Now with LOWERED thresholds for arb-specific trades """
    opportunities = []
    if not markets_kalshi or not markets_polymarket:
        logger.warning("Missing market data for one or both venues")
        return opportunities

    # Match markets across venues (fuzzy matching on question text)
    for km in markets_kalshi:
        kalshi_question = km.get("title", "").lower()
        kalshi_yes_price = km.get("yes_price", 0)
        for pm in markets_polymarket:
            poly_question = pm.get("question", "").lower()
            poly_yes_price = pm.get("yes_price", 0)
            # Fuzzy match (simple word overlap)
            if market_similarity(kalshi_question, poly_question) > 0.7:
                # Calculate arb edge
                edge = calculate_arb_edge(
                    kalshi_yes_price, poly_yes_price,
                    venue_a_fees=0.005,  # Kalshi ~0.5%
                    venue_b_fees=0.005  # Polymarket ~0.5%
                )
                # Apply ARB-SPECIFIC gates (not venue gates)
                if edge["profitable"] and edge["gross_spread_pct"] >= ARB_CONFIG["min_spread_pct"]:
                    opportunities.append({
                        "type": "cross_venue_arb",
                        "kalshi": {
                            "market_id": km.get("id"),
                            "question": km.get("title"),
                            "yes_price": kalshi_yes_price,
                            "venue": "kalshi"
                        },
                        "polymarket": {
                            "market_id": pm.get("id"),
                            "question": pm.get("question"),
                            "yes_price": poly_yes_price,
                            "venue": "polymarket"
                        },
                        "spread_pct": edge["gross_spread_pct"],
                        "net_edge_pct": edge["net_edge_pct"],
                        "recommendation": determine_strategy(kalshi_yes_price, poly_yes_price),
                        "estimated_profit_per_100": edge["net_edge"] * 100,
                        "gates_passed": True,
                        "gate_config": ARB_CONFIG
                    })
                else:
                    logger.debug(f"Arb rejected: spread {edge['gross_spread_pct']:.2%} < {ARB_CONFIG['min_spread_pct']:.2%}")

    # Sort by net edge
    opportunities.sort(key=lambda x: x["net_edge_pct"], reverse=True)
    return opportunities