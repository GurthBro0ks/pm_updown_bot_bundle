"""
DEX Prices - Get prices from DexScreener for arbitrage detection
"""
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"

# Chain name to DexScreener chain ID mapping
CHAIN_MAP = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "polygon": "polygon",
    "avalanche": "avalanche",
    "bsc": "bsc",
}

def get_dex_price(token_address: str, chain: str = "ethereum") -> dict:
    """Get DEX price for a token."""
    chain_id = CHAIN_MAP.get(chain.lower(), chain.lower())
    
    try:
        url = f"{DEXSCREENER_API}/tokens/{chain_id}/{token_address}"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        
        if not data.get("pairs"):
            return None
            
        pair = data["pairs"][0]
        # DexScreener returns priceUsd as STRING - convert to float!
        price_usd = pair.get("priceUsd")
        if price_usd:
            try:
                price_usd = float(price_usd)
            except (ValueError, TypeError):
                price_usd = 0.0
        return {
            "price_usd": price_usd,
            "dex": pair.get("dexId", "unknown"),
            "liquidity": float(pair.get("liquidity", {}).get("usd", 0)),
            "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
            "price_change_24h": float(pair.get("priceChange", {}).get("h24", 0)),
        }
    except Exception as e:
        logger.warning(f"[DEX] Error fetching {chain}:{token_address}: {e}")
        return None

def get_token_prices(tokens: dict) -> dict:
    """Get prices for multiple tokens."""
    prices = {}
    for symbol, info in tokens.items():
        contract = info.get("contract")
        chain = info.get("chain")
        if contract and chain:
            price = get_dex_price(contract, chain)
            if price:
                prices[symbol] = price
                logger.info(f"[DEX] {symbol}: ${price['price_usd']:.4f} on {price['dex']}")
    return prices


def get_token_price_by_symbol(symbol: str, chain: str = None) -> dict:
    """
    Get price for a token by symbol name.
    
    Args:
        symbol: Token symbol (e.g., 'ETH', 'BTC')
        chain: Optional chain filter
    
    Returns:
        Dict with price info or None.
    """
    # TODO: Implement token address lookup
    logger.warning(f"[DEX] get_token_price_by_symbol not fully implemented for {symbol}")
    return None


# Stablecoins - no need to fetch price (always ~$1)
STABLECOINS = {"USDC", "USDT", "DAI", "BUSD", "TUSD", "USDD"}

# Symbol to Coinbase ID mapping
COINBASE_IDS = {
    "ETH": "ETH",
    "BTC": "BTC",
    "SOL": "SOL",
    "BNB": "BNB",
    "XRP": "XRP",
    "ADA": "ADA",
    "DOGE": "DOGE",
    "AVAX": "AVAX",
    "DOT": "DOT",
    "MATIC": "POL",
    "LINK": "LINK",
    "UNI": "UNI",
    "AAVE": "AAVE",
    "MKR": "MKR",
    "LDO": "LDO",
    "ARB": "ARB",
    "OP": "OP",
    "AERO": "AERO",
    "DEGEN": "DEGEN",
}

# Symbol to Kraken ID mapping
KRAKEN_IDS = {
    "ETH": "XETH",
    "BTC": "XXBT",
    "SOL": "SOL",
    "BNB": "XBNT",
    "XRP": "XXRP",
    "ADA": "ADA",
    "DOGE": "XDG",
    "AVAX": "AVAX",
    "DOT": "DOT",
    "MATIC": "MATIC",
    "LINK": "LINK",
    "UNI": "UNI",
    "AAVE": "AAVE",
    "MKR": "MKR",
    "LDO": "LDO",
    "ARB": "ARB",
    "OP": "OP",
}


def get_cex_price(symbol: str) -> Optional[float]:
    """
    Get CEX (centralized exchange) price for comparison.
    Uses Coinbase as primary, Kraken as fallback.
    Skips stablecoins (returns 1.0).

    Args:
        symbol: Token symbol

    Returns:
        Price as float, or None if unavailable.
    """
    # Skip stablecoins
    if symbol.upper() in STABLECOINS:
        return 1.0

    coinbase_id = COINBASE_IDS.get(symbol.upper())
    kraken_id = KRAKEN_IDS.get(symbol.upper())

    # Try Coinbase first
    if coinbase_id:
        try:
            resp = requests.get(
                f"https://api.coinbase.com/v2/prices/{coinbase_id}-USD/spot",
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                price = float(data.get("data", {}).get("amount", 0))
                if price > 0:
                    return price
        except Exception as e:
            logger.debug(f"[CEX] Coinbase error for {symbol}: {e}")

    # Try Kraken as fallback
    if kraken_id:
        try:
            resp = requests.get(
                f"https://api.kraken.com/0/public/Ticker?pair={kraken_id}USD",
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("error") == []:
                    # Kraken returns results in a nested structure
                    result = data.get("result", {})
                    for ticker_data in result.values():
                        if isinstance(ticker_data, dict) and "c" in ticker_data:
                            price = float(ticker_data["c"][0])  # Close price
                            if price > 0:
                                return price
        except Exception as e:
            logger.debug(f"[CEX] Kraken error for {symbol}: {e}")

    logger.warning(f"[CEX] Could not fetch CEX price for {symbol}")
    return None


def check_arbitrage_opportunity(dex_price: float, cex_price: float, min_spread: float = 2.0) -> dict:
    """
    Check if there's an arbitrage opportunity between DEX and CEX.
    
    Args:
        dex_price: Price on DEX
        cex_price: Price on CEX
        min_spread: Minimum spread percentage to report
    
    Returns:
        Dict with arbitrage details or None.
    """
    if not dex_price or not cex_price:
        return None
    
    spread_pct = ((cex_price - dex_price) / dex_price) * 100
    
    if abs(spread_pct) >= min_spread:
        return {
            "spread_pct": spread_pct,
            "dex_price": dex_price,
            "cex_price": cex_price,
            "buy_on": "dex" if dex_price < cex_price else "cex",
            "sell_on": "cex" if dex_price < cex_price else "dex",
            "profit_pct": abs(spread_pct)
        }
    return None


def scan_for_arbitrage(tokens, min_spread: float = 2.0) -> list:
    """
    Scan for arbitrage opportunities across tokens.
    
    Args:
        tokens: Dict of tokens {symbol: {chain, contract}} OR list of token symbols
        min_spread: Minimum spread percentage
    
    Returns:
        List of arbitrage opportunities.
    """
    # Defensive: normalize list to dict
    if isinstance(tokens, list):
        # Convert list of symbols to dict with placeholder chain
        tokens = {sym: {"chain": "ethereum", "contract": None} for sym in tokens}
    
    if not isinstance(tokens, dict):
        logger.warning(f"[ARB] Invalid tokens type: {type(tokens)}, expected dict")
        return []
    
    opportunities = []
    for symbol, info in tokens.items():
        contract = info.get("contract")
        chain = info.get("chain")
        if not contract or not chain:
            continue
        
        dex_price = get_dex_price(contract, chain)
        if not dex_price:
            continue
        
        cex_price = get_cex_price(symbol)
        if not cex_price:
            logger.debug(f"[ARB] {symbol}: no CEX price, skipping")
            continue
        
        # Ensure prices are floats
        try:
            dex_p = float(dex_price.get("price_usd", 0) or 0)
            # Handle both dict and float returns from get_cex_price
            if isinstance(cex_price, dict):
                cex_p = float(cex_price.get("price_usd", 0) or 0)
            else:
                cex_p = float(cex_price or 0)
        except (ValueError, TypeError) as e:
            logger.warning(f"[ARB] {symbol}: price conversion error: {e}")
            continue
        
        if dex_p <= 0 or cex_p <= 0:
            continue
        
        arb = check_arbitrage_opportunity(dex_p, cex_p, min_spread)
        if arb:
            arb["symbol"] = symbol
            arb["chain"] = chain
            opportunities.append(arb)
            logger.info(f"[ARB] {symbol}: {arb['spread_pct']:.2f}% spread")
    
    return opportunities


def get_token_pairs_dexscreener(chain: str = None) -> list:
    """
    Get trading pairs from DexScreener.
    
    Args:
        chain: Optional chain filter
    
    Returns:
        List of trading pairs.
    """
    # TODO: Implement pairs endpoint
    logger.warning("[DEX] get_token_pairs_dexscreener not implemented")
    return []


# Popular tokens for arbitrage scanning
POPULAR_TOKENS = [
    "ETH", "BTC", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC",
    "LINK", "UNI", "AAVE", "MKR", "LDO", "ARB", "OP", "AERO", "DEGEN"
]

if __name__ == "__main__":
    from wallet_tokens import get_wallet_tokens
    tokens = get_wallet_tokens()
    prices = get_token_prices(tokens)
    print(f"Got prices for {len(prices)} tokens")
