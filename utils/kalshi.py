"""
Kalshi API utilities
"""
import os
import requests
import time
from datetime import datetime
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
import base64

def get_kalshi_headers(method, path, api_key, private_key):
    """Generate Kalshi API headers"""
    timestamp = str(int(time.time()))
    msg = f"{timestamp}{method}/trade-api/v2{path}"
    
    signature = private_key.sign(
        msg.encode(),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    sig_b64 = base64.b64encode(signature).decode()
    
    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp
    }

def fetch_kalshi_markets():
    """Fetch open markets from Kalshi API"""
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
    
    if not api_key:
        return []
    
    try:
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        
        headers = get_kalshi_headers('GET', '/markets', api_key, private_key)
        resp = requests.get(
            'https://api.elections.kalshi.com/trade-api/v2/markets',
            headers=headers,
            params={'status': 'open', 'limit': 100},
            timeout=10
        )
        
        if resp.status_code == 200:
            data = resp.json() if resp.text.strip() else {"markets": []}
            markets = []
            for m in data.get('markets', []):
                ticker = m.get('ticker', '')
                yes_bid_cents = m.get('yes_bid', 0)
                yes_ask_cents = m.get('yes_ask', 0)
                if yes_ask_cents <= 0:
                    continue
                yes_price = ((yes_bid_cents + yes_ask_cents) / 2) / 100.0
                no_price = 1.0 - yes_price
                liquidity_usd = m.get('open_interest', 0) * yes_price
                markets.append({
                    "id": ticker,
                    "question": m.get('short_name', ticker),
                    "odds": {"yes": yes_price, "no": no_price},
                    "liquidity_usd": liquidity_usd,
                    "hours_to_end": 48
                })
            return markets
    except Exception as e:
        print(f"Kalshi API error: {e}")
    
    return []
