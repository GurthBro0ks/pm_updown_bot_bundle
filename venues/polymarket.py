"""
Polymarket Venue Adapter
Fetches markets from Polymarket (prediction market platform)
"""

import os
from typing import Dict, List
import requests
from datetime import datetime, timezone

class PolymarketVenue:
    def __init__(self, config: Dict):
        self.base_url = "https://api.thegraph.com/subgraphs/name/polymarket/markets"
        self.api_key = config.get("POLYMARKET_KEY", "")
        self.shadow_mode = config.get("shadow_mode", True)

    def get_markets(self, category: str = "politics") -> List[Dict]:
        """Fetch active markets from Polymarket"""
        
        if not self.api_key:
            print("WARNING: No POLYMARKET_KEY - using mock")
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
            # Use GraphQL via HTTP POST
            query = '''
            {
              markets(where: {category: "politics", active: true}, first: 50) {
                id
                question
                outcomes {
                  id
                  name
                  odds {
                    type
                  value
                  tokenPrice
                  volume
                }
                }
                liquidityMeasure {
                  token {
                  symbol
                }
                volume
              }
              }
            }
            '''
            
            resp = requests.post(
                f"{self.base_url}",
                json={"query": query},
                headers=headers,
                timeout=10
            )
            
            if resp.status_code == 200:
                data = resp.json()
                markets = []
                raw_markets = data.get('data', {}).get('markets', [])
                
                for m in raw_markets:
                    # Extract yes/no odds
                    yes_odds = None
                    no_odds = None
                    yes_price = None
                    no_price = None
                    
                    for outcome in m.get('outcomes', []):
                        if outcome.get('id', '').endswith('_NO'):
                            no_odds = 1 / float(outcome.get('odds', {}).get('value', 1))
                            no_price = outcome.get('odds', {}).get('tokenPrice', 0)
                        elif outcome.get('id', '').endswith('_YES'):
                            yes_odds = 1 / float(outcome.get('odds', {}).get('value', 1))
                            yes_price = outcome.get('odds', {}).get('tokenPrice', 0)
                    
                    if yes_odds and no_odds:
                        yes_price = yes_odds * float(m.get('liquidityMeasure', {}).get('volume', 0))
                    
                    markets.append({
                        "id": m.get('id', ""),
                        "question": m.get('question', ""),
                        "odds": {
                            "yes": yes_odds if yes_odds else 0.5,
                            "no": no_odds if no_odds else 0.5
                        },
                        "liquidity_usd": m.get('liquidityMeasure', {}).get('volume', 0),
                        "hours_to_end": 8760,  # 365 days in hours
                        "fees_pct": 0.01
                    })
                
                print(f"Fetched {len(markets)} **REAL** Polymarket markets")
                return markets
            else:
                print(f"API fail {resp.status_code}: {resp.text[:200]} - mock fallback")
                return []
                
        except Exception as e:
            print(f"Polymarket query error: {e}")
            return []

    def get_odds(self, market_id: str) -> Dict:
        """Get current odds for market"""
        # Implementation placeholder
        return {"yes": 0.5, "no": 0.5}

    def place_order(self, market_id: str, outcome: str, amount: float, shadow: bool = True):
        """Place order (shadow mode default)"""
        if shadow or self.shadow_mode:
            print(f"[SHADOW] Polymarket: {market_id} {outcome} ${amount}")
            return {"status": "shadow", "market_id": market_id}
        # Real order logic (web3 USDC transfer)
        print("Real Polymarket order - IMPLEMENT ME")
        return {"status": "failed", "reason": "Real trades disabled"}
