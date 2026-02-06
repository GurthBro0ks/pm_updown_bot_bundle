#!/usr/bin/env python3
import requests
import sys
import json
from datetime import datetime

def get_orderbook_metrics(pair):
    if pair.upper() != 'SOL/USDC':
        return {'error': 'Unsupported pair: only SOL/USDC supported'}
    
    pool_address = '58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2'  # Raydium SOL-USDC
    dexscreener_url = f'https://api.dexscreener.com/latest/dex/pairs/solana/{pool_address}'
    
    try:
        resp = requests.get(dexscreener_url, timeout=10).json()
        if 'pairs' not in resp or not resp['pairs']:
            return {'error': 'No pair data from Dexscreener'}
        
        pair_data = resp['pairs'][0]
        price_usd = float(pair_data['priceUsd'])
        liq_usd = float(pair_data['liquidity']['usd'])
        vol_h24 = float(pair_data['volume']['h24'])
        
        # Mid price
        mid_price = price_usd
        
        # Jupiter quotes for bid/ask/slippage (0.1 SOL size)
        sol_mint = 'So11111111111111111111111111111111111111112'
        usdc_mint = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
        small_sol = 100000000  # 0.1 SOL lamports
        
        # Sell SOL -> USDC (bid price)
        quote_sell_url = f"https://quote-api.jup.ag/v6/quote?inputMint={sol_mint}&outputMint={usdc_mint}&amount={small_sol}&slippageBps=50"
        quote_sell = requests.get(quote_sell_url, timeout=10).json()
        out_usdc = float(quote_sell['outAmount']) / 1e6
        bid_price = out_usdc / 0.1  # USD per SOL
        
        # Buy SOL -> input USDC for 0.1 SOL at mid
        usdc_for_01sol = int(0.1 * mid_price * 1e6)
        quote_buy_url = f"https://quote-api.jup.ag/v6/quote?inputMint={usdc_mint}&outputMint={sol_mint}&amount={usdc_for_01sol}&slippageBps=50"
        quote_buy = requests.get(quote_buy_url, timeout=10).json()
        out_sol = float(quote_buy['outAmount']) / 1e9
        ask_price = (0.1 * mid_price) / out_sol  # Effective ask USD/SOL
        
        spread_bp = ((ask_price - bid_price) / mid_price) * 10000  # basis points
        
        # Slippage for 10 SOL
        large_sol = 10000000000  # 10 SOL
        quote_large_sell = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint={sol_mint}&outputMint={usdc_mint}&amount={large_sol}&slippageBps=50", timeout=10).json()
        out_large_usdc = float(quote_large_sell['outAmount']) / 1e6
        slippage_10sol_pct = ((mid_price * 10 - out_large_usdc) / (mid_price * 10)) * 100
        
        # VWAP approx: average of small and large
        vwap_10sol = (bid_price * 0.5 + (out_large_usdc / 10) * 0.5)
        
        # Zones: mock support/resistance from price change or simple levels
        zones = [
            {'level': mid_price * 0.99, 'type': 'support'},
            {'level': mid_price * 1.01, 'type': 'resistance'}
        ]
        
        # Scalp signal: tight spread, good liq, volume
        scalp_ok = spread_bp < 5 and liq_usd > 1000000 and vol_h24 > 10000000
        
        # Mock Kelly: assume p=0.55, b=1 (even money), f = p - (1-p)/b = 0.1
        # Tune with slippage: reduce f by slippage factor
        kelly_fraction = max(0.02, 0.1 * (1 - slippage_10sol_pct / 100))
        
        # Mock PnL sim: 10 trades, win 55%, avg win 0.5%, loss 0.5% + slippage
        avg_win = 0.005 - slippage_10sol_pct/100 * 0.5
        avg_loss = -0.005 - slippage_10sol_pct/100 * 0.5
        exp_pnl_pct = 0.55 * avg_win + 0.45 * avg_loss
        sim_pnl = exp_pnl_pct * kelly_fraction * 100  # rough daily % mock
        
        metrics = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'mid_price_usd': mid_price,
            'spread_bp': round(spread_bp, 2),
            'bid_price': round(bid_price, 4),
            'ask_price': round(ask_price, 4),
            'slippage_10sol_pct': round(slippage_10sol_pct, 4),
            'vwap_10sol': round(vwap_10sol, 4),
            'liquidity_usd': liq_usd,
            'volume_h24_usd': vol_h24,
            'zones': zones,
            'scalp_ok': scalp_ok,
            'kelly_fraction': round(kelly_fraction, 4),
            'expected_pnl_pct': round(exp_pnl_pct * 100, 4),
            'gates': {
                'spread': 'PASS' if spread_bp < 10 else 'FAIL',
                'slippage': 'PASS' if slippage_10sol_pct < 2 else 'FAIL',
                'liquidity': 'PASS' if liq_usd > 500000 else 'FAIL',
                'scalp': 'PASS' if scalp_ok else 'FAIL'
            }
        }
        return metrics
    
    except Exception as e:
        return {'error': str(e)}

if __name__ == '__main__':
    pair = sys.argv[1] if len(sys.argv) > 1 else 'SOL/USDC'
    metrics = get_orderbook_metrics(pair)
    print(json.dumps(metrics, indent=2))
