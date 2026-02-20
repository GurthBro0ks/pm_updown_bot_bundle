"""
PredictIt Venue Adapter
Fetches markets from PredictIt (prediction market platform)
"""

import os
from typing import Dict, List
import requests
from datetime import datetime, timezone

class PredictItVenue:
    def __init__(self, config: Dict):
        self.base_url = "https://api.predictit.com/api"
        self.api_key = config.get("PREDICTIT_KEY", "")
        self.shadow_mode = config.get("shadow_mode", True)

    def get_markets(self, category: str = "politics") -> List[Dict]:
        """Fetch active markets from PredictIt"""
        
        if not self.api_key:
            print("WARNING: No PREDICTIT_KEY - using mock")
            # Mock data for testing
            return [
                {"id": "TRUMP-2026.WINNER", "question": "Trump wins 2026", "odds": {"yes": 0.45, "no": 0.55}, "liquidity_usd": 10000, "hours_to_end": 8760, "fees_pct": 0.01},
                {"id": "BIDEN-2026", "question": "Biden wins 2026", "odds": {"yes": 0.55, "no": 0.45}, "liquidity_usd": 8000, "hours_to_end": 8760, "fees_pct": 0.01}
            ]
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        params = {
            "status": "open",
            "limit": 100
        }
        
        try:
            # PredictIt API endpoint (placeholder - actual endpoint may differ)
            resp = requests.get(
                f"{self.base_url}/markets",
                headers=headers,
                params=params,
                timeout=10
            )
            
            if resp.status_code == 200:
                data = resp.json() if resp.text.strip() else {"markets": []}
                # Transform to standard format
                markets = []
                for m in data.get("markets", []):
                    markets.append({
                        "id": m.get("contract_id", ""),
                        "question": m.get("title", ""),
                        "odds": {
                            "yes": m.get("yes_ask", 0.5) / 100,
                            "no": m.get("no_ask", 0.5) / 100
                        },
                        "liquidity_usd": m.get("volume", 0),
                        "hours_to_end": m.get("end_date", 8760),
                        "fees_pct": 0.01
                    })
                print(f"Fetched {len(markets)} **REAL** PredictIt markets")
                return markets
            else:
                print(f"API fail {resp.status_code}: {resp.text[:200]} - mock fallback")
                return []
                
        except Exception as e:
            print(f"PredictIt query error: {e}")
            return []

    def get_odds(self, market_id: str) -> Dict:
        """Get current odds for market"""
        # Implementation placeholder
        return {"yes": 0.5, "no": 0.5}

    def place_order(self, market_id: str, outcome: str, amount: float, shadow: bool = True):
        """Place order (shadow mode default)"""
        if shadow or self.shadow_mode:
            print(f"[SHADOW] PredictIt: {market_id} {outcome} ${amount}")
            return {"status": "shadow", "market_id": market_id}
        # Real order logic (web3 USDC transfer)
        print("Real PredictIt order - IMPLEMENT ME")
        return {"status": "failed", "reason": "Real trades disabled"}
