"""
Wallet Token Discovery - Find all tokens in wallet across chains
"""
import requests
import logging
import os

logger = logging.getLogger(__name__)

WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "0xEA845110a8e8FAE57c5E7Fbe3459DBB7675878a8")

# Lazy import to avoid circular dependency
_wallet_tracker = None

def _get_wallet_tracker():
    global _wallet_tracker
    if _wallet_tracker is None:
        from utils import wallet_tracker
        _wallet_tracker = wallet_tracker
    return _wallet_tracker

# Known tokens from airdrop farming
KNOWN_TOKENS = {
    "AERO": {"chain": "base", "contract": "0x940181a94A35A4569E4529A3CDfB74e38FD98631"},
    "DEGEN": {"chain": "base", "contract": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed"},
    "STG": {"chain": "arbitrum", "contract": "0x6694340fc020c5E6B96567843da2df01b2CE1eb6"},
    "MON": {"chain": "monad", "contract": None},
    "ARB": {"chain": "arbitrum", "contract": "0x912CE59144191C1204E64559FE8253a0e49E6548"},
    "OP": {"chain": "optimism", "contract": "0x4200000000000000000000000000000000000042"},
    "LDO": {"chain": "ethereum", "contract": "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32"},
    "UNI": {"chain": "ethereum", "contract": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"},
    "LINK": {"chain": "ethereum", "contract": "0x514910771AF9Ca656af840dff83E8264EcF986CA"},
}

def get_wallet_tokens(wallet: str = None) -> dict:
    """Get all known tokens in wallet."""
    wallet = wallet or WALLET_ADDRESS
    tokens = {}
    
    # Return known tokens
    for symbol, info in KNOWN_TOKENS.items():
        tokens[symbol] = {
            "symbol": symbol,
            "chain": info["chain"],
            "contract": info["contract"],
            "address": wallet
        }
    
    logger.info(f"[WALLET] Tracking {len(tokens)} known tokens")
    return tokens


def get_holdings_summary() -> dict:
    """
    Get summary of wallet holdings with USD values.

    Returns:
        Dict with total_usd and chains breakdown.
    """
    try:
        tracker = _get_wallet_tracker()
        balance_data = tracker.get_full_balance(WALLET_ADDRESS)

        # Transform wallet_tracker format to expected format
        chains = {}
        for chain, data in balance_data.get("chains", {}).items():
            chains[chain] = {
                "total_usd": data.get("total_usd", 0.0),
                "eth": data.get("eth", 0.0),
                "usdc": data.get("usdc", 0.0),
                "tokens": []
            }

        return {
            "total_usd": balance_data.get("total_usd", 0.0),
            "chains": chains,
            "token_count": sum(len(c.get("tokens", [])) for c in chains.values()),
            "eth_price": balance_data.get("eth_price"),
            "wallet": balance_data.get("wallet"),
        }
    except Exception as e:
        logger.warning(f"[WALLET] Could not fetch real balances, using zero: {e}")
        tokens = get_wallet_tokens()
        chains = {}
        for symbol, info in tokens.items():
            chain = info["chain"]
            if chain not in chains:
                chains[chain] = {"tokens": [], "total_usd": 0.0}
            chains[chain]["tokens"].append({
                "symbol": symbol,
                "usd_value": 0.0
            })
        return {
            "total_usd": 0.0,
            "chains": chains,
            "token_count": len(tokens)
        }


def enrich_with_prices(tokens: dict) -> dict:
    """
    Add USD prices to token dict.
    
    Args:
        tokens: Dict of tokens from get_wallet_tokens
    
    Returns:
        Token dict with price data added.
    """
    # TODO: Add real price fetching
    for symbol, info in tokens.items():
        info["usd_price"] = 0.0
        info["usd_value"] = 0.0
        info["quantity"] = 0.0
    return tokens

if __name__ == "__main__":
    print(f"Wallet: {WALLET_ADDRESS}")
    tokens = get_wallet_tokens()
    for sym, info in tokens.items():
        print(f"  {sym}: {info['chain']}")
