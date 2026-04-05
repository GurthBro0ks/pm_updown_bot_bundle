"""
Track EVM wallet balances across multiple chains.
Uses free public RPCs — no API key needed.
Stores balance history in SQLite for tracking over time.
"""
import re
import requests
import sqlite3
import os
import logging
import argparse
from datetime import datetime
from typing import Optional, Dict, List, Any
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Database path - configurable via env var
DB_PATH = os.environ.get("WALLET_DB_PATH", "/opt/slimy/pm_updown_bot_bundle/paper_trading/wallet.db")

# Default wallet - UPDATE with full address
DEFAULT_WALLET = os.environ.get("WALLET_ADDRESS", "")

# Free public RPCs
# Ethereum has multiple fallbacks due to reliability issues
ETHEREUM_RPCS = [
    "https://rpc.ankr.com/eth",
    "https://ethereum-rpc.publicnode.com",
    "https://eth.llamarpc.com",
    "https://rpc.flashbots.net",
]

CHAINS = {
    "ethereum": {"rpc": "https://eth.llamarpc.com", "explorer": "https://etherscan.io"},
    "base": {"rpc": "https://mainnet.base.org", "explorer": "https://basescan.org"},
    "arbitrum": {"rpc": "https://arb1.arbitrum.io/rpc", "explorer": "https://arbiscan.io"},
    "linea": {"rpc": "https://rpc.linea.build", "explorer": "https://lineascan.build"},
    "optimism": {"rpc": "https://mainnet.optimism.io", "explorer": "https://optimistic.etherscan.io"},
}


def _get_working_rpc(chain: str) -> Optional[str]:
    """Get a working RPC for the given chain with automatic failover."""
    if chain != "ethereum":
        # For non-ethereum chains, use the single RPC from CHAINS
        return CHAINS.get(chain, {}).get("rpc")

    # For ethereum, try multiple RPCs in order
    for rpc_url in ETHEREUM_RPCS:
        try:
            payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
            resp = requests.post(rpc_url, json=payload, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data:
                    logger.debug(f"[WALLET] ETH RPC working: {rpc_url}")
                    return rpc_url
        except Exception:
            continue

    # Fallback to last resort
    logger.warning(f"[WALLET] All ETH RPCs failed, using fallback")
    return ETHEREUM_RPCS[-1]

# USDC contract addresses per chain
USDC_CONTRACTS = {
    "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "linea": "0x176211869cA2b568f2A7D4EE941E073a821EE1ff",
    "optimism": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
}


def _validate_eth_address(address: str) -> str:
    """Validate and normalize an Ethereum address."""
    if not address or not isinstance(address, str):
        raise ValueError(f"Wallet address is empty or not set in .env")
    address = address.strip()
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', address):
        raise ValueError(f"Invalid wallet address: '{address[:20]}...'")
    return address


def get_db() -> sqlite3.Connection:
    """Get database connection. Creates DB and tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                chain TEXT NOT NULL,
                eth_balance REAL DEFAULT 0,
                usdc_balance REAL DEFAULT 0,
                eth_usd REAL DEFAULT 0,
                usdc_usd REAL DEFAULT 0,
                total_usd REAL DEFAULT 0,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallet_chain_time
            ON wallet_snapshots(wallet, chain, timestamp)
        """)

        conn.commit()
    finally:
        conn.close()


def get_eth_balance(chain: str, wallet: str) -> Optional[float]:
    """Get native ETH balance on a chain."""
    rpc_url = _get_working_rpc(chain)
    if not rpc_url:
        return None
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [wallet, "latest"],
        "id": 1
    }
    
    try:
        resp = requests.post(rpc_url, json=payload, timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"[WALLET] {chain} request failed: {e}")
        return None
    
    if resp.status_code != 200:
        logger.error(f"[WALLET] {chain} HTTP {resp.status_code}")
        return None
    
    data = resp.json()
    if "error" in data:
        err = data["error"]
        logger.error(f"[WALLET] {chain} RPC error {err.get('code')}: {err.get('message')}")
        return None
    
    result = data.get("result")
    if result is None:
        logger.error(f"[WALLET] {chain} no 'result' in response")
        return None
    
    try:
        balance_eth = int(result, 16) / 10**18
    except (ValueError, TypeError) as e:
        logger.error(f"[WALLET] {chain} hex conversion failed: {e}")
        balance_eth = 0.0
    
    return balance_eth


def get_usdc_balance(chain: str, wallet: str) -> Optional[float]:
    """Get USDC balance on a chain."""
    rpc_url = _get_working_rpc(chain)
    contract = USDC_CONTRACTS.get(chain)
    if not rpc_url or not contract:
        return None
    
    # ERC-20 balanceOf(address) call
    data_payload = f"0x70a08231000000000000000000000000{wallet[2:]}"
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": contract, "data": data_payload}, "latest"],
        "id": 1
    }
    
    try:
        resp = requests.post(rpc_url, json=payload, timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"[WALLET] {chain} request failed: {e}")
        return None
    
    if resp.status_code != 200:
        logger.error(f"[WALLET] {chain} HTTP {resp.status_code}")
        return None
    
    data = resp.json()
    if "error" in data:
        err = data["error"]
        logger.error(f"[WALLET] {chain} RPC error {err.get('code')}: {err.get('message')}")
        return None
    
    result = data.get("result")
    if result is None:
        logger.error(f"[WALLET] {chain} no 'result' in response")
        return None
    
    try:
        balance_usdc = int(result, 16) / 10**6  # USDC has 6 decimals
    except (ValueError, TypeError) as e:
        logger.error(f"[WALLET] {chain} hex conversion failed: {e}")
        balance_usdc = 0.0
    
    return balance_usdc


def get_eth_price() -> Optional[float]:
    """Get current ETH price from CoinGecko (free, no key)."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd",
            timeout=10
        )
        return resp.json()["ethereum"]["usd"]
    except Exception as e:
        logger.error(f"[WALLET] ETH price fetch error: {e}")
        return None


def get_full_balance(wallet: str = None) -> Dict[str, Any]:
    """Get all balances across all chains."""
    if not wallet:
        wallet = DEFAULT_WALLET

    if not wallet:
        raise ValueError("No wallet address provided. Set WALLET_ADDRESS env var.")
    
    # Validate wallet address
    wallet = _validate_eth_address(wallet)

    balances = {}
    total_usd = 0
    eth_price = get_eth_price()

    for chain in CHAINS:
        eth = get_eth_balance(chain, wallet)
        usdc = get_usdc_balance(chain, wallet)

        if eth is None:
            logger.warning(f"[WALLET] {chain}: ETH balance RPC returned None (check RPC connectivity)")
        if usdc is None:
            logger.warning(f"[WALLET] {chain}: USDC balance RPC returned None (check RPC connectivity)")

        eth_usd = eth * eth_price if eth and eth_price else 0
        usdc_usd = usdc or 0

        balances[chain] = {
            "eth": eth or 0,
            "eth_usd": round(eth_usd, 2),
            "usdc": usdc or 0,
            "total_usd": round(eth_usd + usdc_usd, 2),
        }
        total_usd += eth_usd + usdc_usd

    return {
        "wallet": wallet,
        "chains": balances,
        "total_usd": round(total_usd, 2),
        "eth_price": eth_price,
        "timestamp": datetime.now().isoformat()
    }


def snapshot_balances(wallet: str = None) -> bool:
    """Record wallet balances to SQLite database."""
    if not wallet:
        wallet = DEFAULT_WALLET

    if not wallet:
        logger.warning("[WALLET] No wallet address for snapshot")
        return False
    
    # Validate wallet address before calling get_full_balance
    try:
        wallet = _validate_eth_address(wallet)
    except ValueError as e:
        logger.error(f"[WALLET] {e}")
        return False

    try:
        balance_data = get_full_balance(wallet)
    except Exception as e:
        logger.error(f"[WALLET] Failed to get balances: {e}")
        return False

    conn = get_db()
    try:
        for chain, data in balance_data["chains"].items():
            usdc_usd = data.get("usdc", 0)  # USDC is already in USD
            conn.execute("""
                INSERT INTO wallet_snapshots
                (wallet, chain, eth_balance, usdc_balance, eth_usd, usdc_usd, total_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                wallet,
                chain,
                data["eth"],
                data["usdc"],
                data["eth_usd"],
                usdc_usd,
                data["total_usd"]
            ))
        conn.commit()
        logger.info(f"[WALLET] Snapshot saved for {wallet}")
        return True
    except Exception as e:
        logger.error(f"[WALLET] Failed to save snapshot: {e}")
        return False
    finally:
        conn.close()


def get_balance_history(wallet: str = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Get balance history from database."""
    if not wallet:
        wallet = DEFAULT_WALLET

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT chain, eth_balance, usdc_balance, total_usd, timestamp
            FROM wallet_snapshots
            WHERE wallet = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (wallet, limit)).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_latest_snapshots(wallet: str = None) -> Dict[str, Dict[str, Any]]:
    """Get latest snapshot per chain."""
    if not wallet:
        wallet = DEFAULT_WALLET

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT chain, eth_balance, usdc_balance, total_usd, timestamp
            FROM wallet_snapshots s1
            WHERE wallet = ? AND timestamp = (
                SELECT MAX(timestamp) FROM wallet_snapshots s2
                WHERE s1.wallet = s2.wallet AND s1.chain = s2.chain
            )
        """, (wallet,)).fetchall()

        return {row["chain"]: dict(row) for row in rows}
    finally:
        conn.close()


# Initialize database on module import
init_db()


def main():
    """CLI entry point for snapshots."""
    parser = argparse.ArgumentParser(description="Wallet balance tracker")
    parser.add_argument("--snapshot", action="store_true", help="Take a balance snapshot")
    parser.add_argument("--wallet", type=str, help="Wallet address override")
    parser.add_argument("--history", action="store_true", help="Show balance history")
    parser.add_argument("--limit", type=int, default=30, help="History limit")
    args = parser.parse_args()

    wallet = args.wallet or DEFAULT_WALLET

    if args.snapshot:
        success = snapshot_balances(wallet)
        if success:
            print(f"✓ Snapshot saved for {wallet}")
        else:
            print(f"✗ Failed to save snapshot")
    elif args.history:
        history = get_balance_history(wallet, args.limit)
        print(f"Balance history for {wallet}:")
        for row in history:
            print(f"  {row['chain']}: {row['eth_balance']:.4f} ETH, ${row['total_usd']:.2f} at {row['timestamp']}")
    else:
        # Default: show current balance
        balance = get_full_balance(wallet)
        print(f"Wallet: {balance['wallet']}")
        print(f"ETH Price: ${balance['eth_price']}")
        print(f"Total: ${balance['total_usd']}")
        for chain, data in balance["chains"].items():
            print(f"  {chain}: {data['eth']:.4f} ETH (${data['eth_usd']}) + ${data['usdc']:.2f} USDC = ${data['total_usd']}")


if __name__ == "__main__":
    main()
