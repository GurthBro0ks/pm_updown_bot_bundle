"""
Hyperliquid Venue Adapter
Fetches markets from Hyperliquid (off-chain order book with 0.01% maker fees)
"""

import os
import logging
from typing import Dict, List
import requests
from datetime import datetime, timezone

class HyperliquidVenue:
    def __init__(self, config: Dict):
        self.base_url = "https://api.hyperliquid.com"
        self.api_key = config.get("HYPER_KEY", "")
        self.shadow_mode = config.get("SHADOW_MODE", "True") == "True"

    def get_markets(self, category: str = "politics") -> List[Dict]:
        """Fetch markets from Hyperliquid off-chain order book"""
        
        if not self.api_key:
            print("WARNING: No HYPER_KEY - using mock")
            # Mock data for testing
            return [
                {"id": "TRUMP-WINNER-2026", "question": "Trump wins 2026", "odds": {"yes": 0.48, "no": 0.52}, "liquidity_usd": 50000, "hours_to_end": 8760, "fees_pct": 0.01},
                {"id": "BIDEN-WINS-2026", "question": "Biden wins 2026", "odds": {"yes": 0.52, "no": 0.48}, "liquidity_usd": 40000, "hours_to_end": 8760, "fees_pct": 0.01}
            ]
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Hyperliquid uses REST API (no GraphQL query needed)
        endpoint = "/market/order-book"
        params = {
            "status": "open",
            "limit": 100
        }
        
        try:
            resp = requests.get(
                f"{self.base_url}{endpoint}",
                headers=headers,
                params=params,
                timeout=10
            )
            
            if resp.status_code == 200:
                data = resp.json()
                markets = []
                
                # Hyperliquid order book structure
                # Each market has order book with bids/asks
                if "data" in data:
                    for m in data.get("data", [])[:50]:  # First 50 markets
                        # Get best bid and best ask for mid-price
                        bids = m.get("bids", [])
                        asks = m.get("asks", [])
                        
                        if not bids or not asks:
                            continue
                        
                        best_bid = sorted(bids, key=lambda x: x.get("price", 0), reverse=True)[0]
                        best_ask = sorted(asks, key=lambda x: x.get("price", 0))[0]
                        
                        # Calculate mid-price
                        if best_bid and best_ask:
                            mid_price = (best_bid.get("price", 0) + best_ask.get("price", 0)) / 2
                        else:
                            mid_price = m.get("price", 0)  # Use price if one-sided book
                        
                        # Standardize to yes/no format (like Kalshi)
                        yes_price = mid_price
                        no_price = 1.0 - mid_price
                        
                        markets.append({
                            "id": m.get("slug", ""),
                            "question": m.get("question", m.get("slug", "")),
                            "odds": {
                                "yes": round(yes_price, 4),  # 4 decimal places
                                "no": round(no_price, 4)
                            },
                            "liquidity_usd": m.get("liquidity_usd", 0),
                            "hours_to_end": 8760,  # 365 days
                            "fees_pct": 0.01  # 0.01% flat fee
                        })
                
                logger.info(f"Hyperliquid: {len(markets)} markets")
                return markets
            else:
                logger.error(f"API fail {resp.status_code}: {resp.text[:200]}")
                return []
        
        except Exception as e:
            logger.error(f"Hyperliquid API error: {e}")
            return []
