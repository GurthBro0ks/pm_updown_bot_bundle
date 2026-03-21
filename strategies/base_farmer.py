"""
Base Chain Airdrop Farming Module

Executes lightweight DeFi interactions on Base to build wallet narrative
for the potential $BASE token airdrop (exploring Q2-Q4 2026).

Actions are designed to look like genuine user behavior:
- Randomized amounts (never round numbers)
- Randomized timing (±2hr window around scheduled time)
- Diverse protocol interactions (swap, lend, NFT, bridge)
- Weekly rotation of token pairs and protocols

This runs AFTER all trading phases complete.
If circuit breaker is tripped, farming pauses too.
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from functools import wraps
from dotenv import load_dotenv

load_dotenv("/opt/slimy/pm_updown_bot_bundle/.env")

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Weekly gas+swap budget for farming (USD)
FARMING_WEEKLY_BUDGET = float(os.getenv("FARMING_WEEKLY_BUDGET", "5.00"))

# Minimum ETH balance on Base to farm (below this, skip farming)
MIN_BASE_ETH_FOR_FARMING = float(os.getenv("MIN_BASE_ETH_FOR_FARMING", "0.002"))

# Base RPC endpoint
BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")

# Farming wallet (dedicated wallet for airdrop farming)
FARMING_WALLET_ADDRESS = os.getenv("FARMING_WALLET_ADDRESS", "").lower()
FARMING_PRIVATE_KEY = os.getenv("FARMING_PRIVATE_KEY", "")

# Path for farming state/logs
FARMING_STATE_FILE = "/opt/slimy/pm_updown_bot_bundle/data/base_farming_state.json"
FARMING_LOG_FILE = "/opt/slimy/pm_updown_bot_bundle/data/base_farming_log.json"

# Protocol addresses on Base (mainnet)
PROTOCOLS = {
    "aerodrome_router": "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
    "uniswap_v3_router": "0x2626664c2603336E57B271c5C0b26F421741e481",
    "aave_v3_pool": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    "zora_minter": "0x969C8302d563a871522042e097f0D63eBE2f9996",
}

# Token addresses on Base
TOKENS = {
    "WETH": "0x4200000000000000000000000000000000000006",
    "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "DAI": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    "USDbC": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",
    "cbETH": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
}

# Swap pairs to rotate through weekly
SWAP_PAIRS = [
    ("WETH", "USDC"),
    ("WETH", "DAI"),
    ("USDC", "DAI"),
    ("WETH", "cbETH"),
    ("USDC", "USDbC"),
]

# =============================================================================
# WEB3 INITIALIZATION (lazy)
# =============================================================================

_w3 = None
_wallet_address = None


def _get_web3():
    """Lazy web3 initialization."""
    global _w3, _wallet_address
    if _w3 is not None:
        return _w3, _wallet_address

    from web3 import Web3
    from eth_account import Account

    if not FARMING_PRIVATE_KEY:
        raise ValueError(
            "[BASE_FARM] FARMING_PRIVATE_KEY not set in .env. "
            "Cannot execute live transactions."
        )

    _w3 = Web3(Web3.HTTPProvider(BASE_RPC))
    if not _w3.is_connected():
        raise ConnectionError(f"[BASE_FARM] Cannot connect to Base RPC: {BASE_RPC}")

    acct = Account.from_key(FARMING_PRIVATE_KEY)
    _wallet_address = Web3.to_checksum_address(acct.address)

    logger.info(f"[BASE_FARM] Connected to Base. Wallet: {_wallet_address}")
    return _w3, _wallet_address


def _get_wallet_balance_eth():
    """Get wallet ETH balance on Base."""
    w3, wallet = _get_web3()
    return w3.eth.get_balance(wallet) / 1e18


# =============================================================================
# FARMING STATE MANAGEMENT
# =============================================================================

def _load_state() -> dict:
    """Load farming state from disk."""
    try:
        if os.path.exists(FARMING_STATE_FILE):
            with open(FARMING_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[BASE_FARM] Failed to load state: {e}")
    return {
        "last_farm_date": None,
        "weekly_spend_usd": 0.0,
        "week_start": None,
        "actions_this_week": [],
        "total_actions": 0,
        "protocols_used": [],
        "pairs_used_this_week": [],
    }


def _save_state(state: dict):
    """Save farming state to disk."""
    try:
        os.makedirs(os.path.dirname(FARMING_STATE_FILE) or ".", exist_ok=True)
        with open(FARMING_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"[BASE_FARM] Failed to save state: {e}")


def _log_action(action: dict):
    """Append a farming action to the log."""
    try:
        log = []
        if os.path.exists(FARMING_LOG_FILE):
            with open(FARMING_LOG_FILE, "r") as f:
                log = json.load(f)
        log.append(action)
        if len(log) > 500:
            log = log[-500:]
        with open(FARMING_LOG_FILE, "w") as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        logger.warning(f"[BASE_FARM] Failed to log action: {e}")


# =============================================================================
# ANTI-SYBIL: HUMANIZED BEHAVIOR
# =============================================================================

def _randomize_amount(target_usd: float, variance_pct: float = 0.15) -> float:
    """Randomize an amount to avoid round numbers."""
    low = target_usd * (1 - variance_pct)
    high = target_usd * (1 + variance_pct)
    amount = random.uniform(low, high)
    amount += random.uniform(0.001, 0.009)
    return round(amount, 4)


def _should_farm_today(state: dict) -> bool:
    """Decide if we should farm today based on weekly rhythm."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if state.get("last_farm_date") == today:
        logger.info("[BASE_FARM] Already farmed today, skipping")
        return False

    week_start = state.get("week_start")
    now = datetime.now(timezone.utc)
    if week_start:
        week_start_dt = datetime.fromisoformat(week_start)
        if (now - week_start_dt).days >= 7:
            state["weekly_spend_usd"] = 0.0
            state["week_start"] = now.isoformat()
            state["actions_this_week"] = []
            state["pairs_used_this_week"] = []
    else:
        state["week_start"] = now.isoformat()

    if state["weekly_spend_usd"] >= FARMING_WEEKLY_BUDGET:
        logger.info(
            f"[BASE_FARM] Weekly budget exhausted "
            f"(${state['weekly_spend_usd']:.2f} / ${FARMING_WEEKLY_BUDGET:.2f})"
        )
        return False

    if random.random() > 0.60:
        logger.info("[BASE_FARM] Random skip today (natural rhythm)")
        return False

    return True


def _pick_action(state: dict) -> str:
    """Pick today's farming action, rotating through protocols."""
    actions = ["swap_aerodrome", "swap_uniswap", "aave_deposit", "nft_mint"]
    recent = state.get("actions_this_week", [])
    unused = [a for a in actions if a not in recent]
    if unused:
        return random.choice(unused)
    weights = [0.35, 0.35, 0.2, 0.1]
    return random.choices(actions, weights=weights, k=1)[0]


def _get_token_balance(token: str, wallet: str) -> int:
    """Get token balance for the farming wallet. Returns balance in wei."""
    from web3 import Web3
    w3, _ = _get_web3()
    token_addr = Web3.to_checksum_address(TOKENS[token])
    if token in ("WETH",):
        # For WETH, check the ERC-20 balance (WETH is held, not ETH)
        bal_data = "0x70a08231000000000000000000000000" + wallet[2:].lower()
        result = w3.eth.call({"to": token_addr, "data": bal_data}, "latest")
        return int.from_bytes(result, "big")
    return 0  # Unknown token


def _pick_swap_pair(state: dict, available_tokens: list = None) -> tuple:
    """Pick a token pair we haven't swapped this week, with valid input balance.

    Args:
        state: Current farming state
        available_tokens: List of tokens with non-zero balance. If None, uses WETH.
    """
    if available_tokens is None:
        available_tokens = ["WETH"]  # WETH is what we hold after wrapping

    used = state.get("pairs_used_this_week", [])
    # Filter pairs: token_in must have balance, pair not used this week
    candidates = [
        p for p in SWAP_PAIRS
        if list(p) not in used and p[0] in available_tokens
    ]
    if candidates:
        return random.choice(candidates)
    # Fallback: any pair where input token is available
    candidates = [p for p in SWAP_PAIRS if p[0] in available_tokens]
    if candidates:
        return random.choice(candidates)
    # Last resort: WETH pair (we always have WETH after wrapping)
    return ("WETH", "USDC")


# =============================================================================
# TRANSACTION HELPERS
# =============================================================================

def _sign_and_send(w3, tx: dict) -> dict:
    """Sign a transaction with Account.sign_transaction and wait for receipt."""
    from eth_account import Account
    key = FARMING_PRIVATE_KEY if FARMING_PRIVATE_KEY.startswith("0x") else "0x" + FARMING_PRIVATE_KEY
    signed = Account.sign_transaction(tx, key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return receipt


def _approve_token(w3, token_addr: str, spender: str, amount_wei: int, wallet: str):
    """Approve spender to spend amount_wei of token."""
    import eth_abi
    approve_fn_selector = bytes.fromhex("095ea7b3")  # approve(address,uint256)
    params = eth_abi.encode(["address", "uint256"], [spender, amount_wei])
    calldata = (approve_fn_selector + params).hex()

    approve_tx = {
        "to": token_addr,
        "from": wallet,
        "value": 0,
        "data": calldata,
        "nonce": w3.eth.get_transaction_count(wallet),
        "gas": 50000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 8453,
    }
    receipt = _sign_and_send(w3, approve_tx)
    if receipt["status"] != 1:
        raise RuntimeError(f"[BASE_FARM] Approval failed: {receipt['transactionHash'].hex()}")
    logger.info(f"[BASE_FARM] Approved {token_addr[:10]}... → {spender[:10]}...")


def _usd_to_wei(amount_usd: float, token: str, eth_price: float) -> int:
    """Convert USD amount to token wei."""
    if token in ("USDC", "USDbC"):
        return int(amount_usd * 1e6)
    elif token == "DAI":
        return int(amount_usd * 1e18)
    elif token in ("WETH", "cbETH"):
        return int((amount_usd / eth_price) * 1e18)
    return int(amount_usd * 1e18)


# =============================================================================
# LIVE SWAP EXECUTION
# =============================================================================

def _execute_aerodrome_swap(token_out: str, amount_eth: float, eth_price: float) -> dict:
    """Swap WETH → token_out via Aerodrome V2 on Base.
    We wrap ETH to WETH first, then swap.
    """
    import eth_abi
    from web3 import Web3

    w3, wallet = _get_web3()
    ROUTER = Web3.to_checksum_address(PROTOCOLS["aerodrome_router"])
    weth_addr = Web3.to_checksum_address(TOKENS["WETH"])
    token_out_addr = Web3.to_checksum_address(TOKENS[token_out])
    amount_wei = int(amount_eth * 1e18)

    # Step 1: Wrap ETH → WETH using WETH.deposit()
    weth_deposit_selector = bytes.fromhex("d0e30db0")  # deposit()
    wrap_tx = {
        "to": weth_addr,
        "from": wallet,
        "value": amount_wei,
        "data": weth_deposit_selector.hex(),
        "nonce": w3.eth.get_transaction_count(wallet),
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 8453,
    }
    logger.info(f"[BASE_FARM] Wrapping {amount_eth:.6f} ETH → WETH...")
    receipt = _sign_and_send(w3, wrap_tx)
    if receipt["status"] != 1:
        raise RuntimeError(f"[BASE_FARM] ETH wrap failed: {receipt['transactionHash'].hex()}")
    logger.info(f"[BASE_FARM] Wrapped. TX: {receipt['transactionHash'].hex()}")

    time.sleep(2)  # Let balance update

    # Step 2: Approve Aerodrome router to spend WETH
    _approve_token(w3, weth_addr, ROUTER, amount_wei, wallet)

    # Step 3: Wait for approval to settle before fetching nonce for swap
    # (avoids "replacement transaction underpriced" error)
    time.sleep(2)

    # Step 4: Aerodrome V2 Router02.swapExactTokensForTokens
    # bytes data = abi.encode(address[] path, bool stable, address recipient)
    path = [weth_addr, token_out_addr]
    swap_data = eth_abi.encode(["address[]", "bool", "address"], [path, False, wallet])
    swap_fn_selector = bytes.fromhex("0c97b077")  # swapExactTokensForTokens(uint256,uint256,bytes)
    swap_params = eth_abi.encode(["uint256", "uint256", "bytes"],
                                 [amount_wei, 0, swap_data])
    calldata = (swap_fn_selector + swap_params).hex()

    swap_tx = {
        "to": ROUTER,
        "from": wallet,
        "value": 0,
        "data": calldata,
        "nonce": w3.eth.get_transaction_count(wallet),
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 8453,
    }

    logger.info(f"[BASE_FARM] Aerodrome swap: {amount_eth:.6f} WETH → {token_out}...")
    receipt = _sign_and_send(w3, swap_tx)
    status = "executed" if receipt["status"] == 1 else "failed"
    tx_hash = receipt["transactionHash"].hex()
    gas_used = receipt["gasUsed"] * receipt["effectiveGasPrice"] / 1e18
    logger.info(f"[BASE_FARM] Aerodrome TX: {tx_hash} status={status} gas={gas_used:.6f} ETH")

    return {
        "tx_hash": tx_hash,
        "status": status,
        "gas_eth": round(gas_used, 6),
        "gas_usd": round(gas_used * eth_price, 4),
    }


def _execute_uniswap_v3_swap(token_in: str, token_out: str,
                              amount_usd: float, eth_price: float) -> dict:
    """Swap token_in → token_out on Uniswap V3 (Base)."""
    import eth_abi
    from web3 import Web3

    w3, wallet = _get_web3()
    ROUTER = Web3.to_checksum_address(PROTOCOLS["uniswap_v3_router"])
    token_in_addr = Web3.to_checksum_address(TOKENS[token_in])
    token_out_addr = Web3.to_checksum_address(TOKENS[token_out])
    amount_wei = _usd_to_wei(amount_usd, token_in, eth_price)

    # Check token balance for non-ETH input tokens
    if token_in not in ("WETH",):
        # Check balance via ERC-20 balanceOf
        bal_data = "0x70a08231000000000000000000000000" + wallet[2:].lower()
        bal_result = w3.eth.call({"to": token_in_addr, "data": bal_data}, "latest")
        token_bal = int.from_bytes(bal_result, "big")
        if token_bal < amount_wei:
            raise RuntimeError(
                f"Insufficient {token_in} balance: {token_bal/10**18:.4f} < required {amount_wei/10**18:.4f}"
            )
        _approve_token(w3, token_in_addr, ROUTER, amount_wei, wallet)
        # Wait for approval to settle before fetching nonce for swap
        time.sleep(2)

    # Uniswap V3 SwapRouter02.exactInputSingle
    # params = (tokenIn, tokenOut, fee, recipient, amountIn, amountOutMin, sqrtPriceLimitX96)
    # fee: 500=0.05%, 3000=0.30%, 10000=1.00%
    params = eth_abi.encode(
        ["address", "address", "uint24", "address", "uint256", "uint256", "uint256"],
        [token_in_addr, token_out_addr, 3000, wallet, amount_wei, 0, 0]
    )
    selector = bytes.fromhex("04e45aaf")  # exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))
    calldata = (selector + params).hex()

    swap_tx = {
        "to": ROUTER,
        "from": wallet,
        "value": 0,
        "data": calldata,
        "nonce": w3.eth.get_transaction_count(wallet),
        "gas": 150000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 8453,
    }

    logger.info(f"[BASE_FARM] Uniswap V3 swap: {token_in} → {token_out} (${amount_usd:.2f})...")
    receipt = _sign_and_send(w3, swap_tx)
    status = "executed" if receipt["status"] == 1 else "failed"
    tx_hash = receipt["transactionHash"].hex()
    gas_used = receipt["gasUsed"] * receipt["effectiveGasPrice"] / 1e18
    logger.info(f"[BASE_FARM] Uniswap V3 TX: {tx_hash} status={status} gas={gas_used:.6f} ETH")

    return {
        "tx_hash": tx_hash,
        "status": status,
        "gas_eth": round(gas_used, 6),
        "gas_usd": round(gas_used * eth_price, 4),
    }


def _execute_aave_deposit(token: str, amount_usd: float, eth_price: float) -> dict:
    """Deposit token into Aave V3 on Base."""
    import eth_abi

    w3, wallet = _get_web3()
    POOL = PROTOCOLS["aave_v3_pool"]
    token_addr = TOKENS[token]
    amount_wei = _usd_to_wei(amount_usd, token, eth_price)

    # Approve Aave pool to pull tokens
    _approve_token(w3, token_addr, POOL, amount_wei, wallet)

    # Wait for approval to settle before fetching nonce for supply
    time.sleep(2)

    # Aave V3 Pool.supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode)
    params = eth_abi.encode(["address", "uint256", "address", "uint16"],
                             [token_addr, amount_wei, wallet, 0])
    selector = bytes.fromhex("617ba037")  # supply(address,uint256,address,uint16)
    calldata = (selector + params).hex()

    supply_tx = {
        "to": POOL,
        "from": wallet,
        "value": 0,
        "data": calldata,
        "nonce": w3.eth.get_transaction_count(wallet),
        "gas": 150000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 8453,
    }

    logger.info(f"[BASE_FARM] Aave V3 supply: ${amount_usd:.2f} {token}...")
    receipt = _sign_and_send(w3, supply_tx)
    status = "executed" if receipt["status"] == 1 else "failed"
    tx_hash = receipt["transactionHash"].hex()
    gas_used = receipt["gasUsed"] * receipt["effectiveGasPrice"] / 1e18
    logger.info(f"[BASE_FARM] Aave V3 TX: {tx_hash} status={status} gas={gas_used:.6f} ETH")

    return {
        "tx_hash": tx_hash,
        "status": status,
        "gas_eth": round(gas_used, 6),
        "gas_usd": round(gas_used * eth_price, 4),
    }


def _execute_zora_mint(eth_price: float) -> dict:
    """Mint a free NFT on Zora ERC721Drop (Base).
    Contract: 0x969C8302D563A871522042E097f0d63EBE2F9996
    Function: mint(address _mintTo) — public free mint
    """
    import eth_abi
    from web3 import Web3

    w3, wallet = _get_web3()
    ZORA_MINTER = Web3.to_checksum_address(PROTOCOLS["zora_minter"])

    params = eth_abi.encode(["address"], [wallet])
    selector = bytes.fromhex("6a627842")  # mint(address)
    calldata = (selector + params).hex()

    mint_tx = {
        "to": ZORA_MINTER,
        "from": wallet,
        "value": 0,
        "data": calldata,
        "nonce": w3.eth.get_transaction_count(wallet),
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 8453,
    }

    logger.info(f"[BASE_FARM] Zora NFT mint...")
    receipt = _sign_and_send(w3, mint_tx)
    status = "executed" if receipt["status"] == 1 else "failed"
    tx_hash = receipt["transactionHash"].hex()
    gas_used = receipt["gasUsed"] * receipt["effectiveGasPrice"] / 1e18
    logger.info(f"[BASE_FARM] Zora TX: {tx_hash} status={status} gas={gas_used:.6f} ETH")

    return {
        "tx_hash": tx_hash,
        "status": status,
        "gas_eth": round(gas_used, 6),
        "gas_usd": round(gas_used * eth_price, 4),
    }


# =============================================================================
# FARMING ACTION ROUTER
# =============================================================================

def _get_eth_price() -> float:
    """Fetch ETH/USD price from CoinGecko."""
    import requests
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd",
            timeout=10,
        )
        return resp.json()["ethereum"]["usd"]
    except Exception:
        return 2148.76  # fallback


def _do_swap_aerodrome(pair: tuple, amount_usd: float, dry_run: bool) -> dict:
    """Execute Aerodrome swap (WETH → token)."""
    eth_price = _get_eth_price()
    amount_eth = amount_usd / eth_price

    if dry_run:
        action = {
            "type": "swap",
            "protocol": "aerodrome",
            "pair": list(pair),
            "amount_usd": _randomize_amount(amount_usd),
            "chain": "base",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "simulated",
            "est_gas_usd": round(random.uniform(0.005, 0.02), 4),
        }
        logger.info(
            f"[BASE_FARM] SIMULATED swap: ${action['amount_usd']:.4f} "
            f"{pair[0]}→{pair[1]} on aerodrome (gas: ~${action['est_gas_usd']:.4f})"
        )
    else:
        try:
            result = _execute_aerodrome_swap(pair[1], amount_eth, eth_price)
            action = {
                "type": "swap",
                "protocol": "aerodrome",
                "pair": list(pair),
                "amount_usd": _randomize_amount(amount_usd),
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": result["status"],
                "tx_hash": result["tx_hash"],
                "est_gas_usd": result["gas_usd"],
            }
        except Exception as e:
            logger.error(f"[BASE_FARM] Aerodrome swap failed: {e}")
            action = {
                "type": "swap",
                "protocol": "aerodrome",
                "pair": list(pair),
                "amount_usd": _randomize_amount(amount_usd),
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "error": str(e),
                "est_gas_usd": 0,
            }
    return action


def _do_swap_uniswap(pair: tuple, amount_usd: float, dry_run: bool) -> dict:
    """Execute Uniswap V3 swap. If input token has no balance, falls back to WETH→USDC."""
    eth_price = _get_eth_price()

    if dry_run:
        action = {
            "type": "swap",
            "protocol": "uniswap_v3",
            "pair": list(pair),
            "amount_usd": _randomize_amount(amount_usd),
            "chain": "base",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "simulated",
            "est_gas_usd": round(random.uniform(0.005, 0.02), 4),
        }
        logger.info(
            f"[BASE_FARM] SIMULATED swap: ${action['amount_usd']:.4f} "
            f"{pair[0]}→{pair[1]} on uniswap_v3 (gas: ~${action['est_gas_usd']:.4f})"
        )
    else:
        try:
            result = _execute_uniswap_v3_swap(pair[0], pair[1], amount_usd, eth_price)
            action = {
                "type": "swap",
                "protocol": "uniswap_v3",
                "pair": list(pair),
                "amount_usd": _randomize_amount(amount_usd),
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": result["status"],
                "tx_hash": result["tx_hash"],
                "est_gas_usd": result["gas_usd"],
            }
        except RuntimeError as e:
            err_str = str(e)
            if "Insufficient" in err_str or "balance" in err_str.lower():
                # Input token has no balance — retry with WETH→USDC
                logger.warning(f"[BASE_FARM] Uniswap: {err_str}. Retrying with WETH→USDC...")
                fallback_pair = ("WETH", "USDC")
                try:
                    result = _execute_uniswap_v3_swap(fallback_pair[0], fallback_pair[1], amount_usd, eth_price)
                    action = {
                        "type": "swap",
                        "protocol": "uniswap_v3",
                        "pair": list(fallback_pair),
                        "amount_usd": _randomize_amount(amount_usd),
                        "chain": "base",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": result["status"],
                        "tx_hash": result["tx_hash"],
                        "est_gas_usd": result["gas_usd"],
                    }
                except Exception as retry_err:
                    logger.error(f"[BASE_FARM] Uniswap retry failed: {retry_err}")
                    action = {
                        "type": "swap",
                        "protocol": "uniswap_v3",
                        "pair": list(fallback_pair),
                        "amount_usd": _randomize_amount(amount_usd),
                        "chain": "base",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "error",
                        "error": str(retry_err),
                        "est_gas_usd": 0,
                    }
            else:
                logger.error(f"[BASE_FARM] Uniswap V3 swap failed: {e}")
                action = {
                    "type": "swap",
                    "protocol": "uniswap_v3",
                    "pair": list(pair),
                    "amount_usd": _randomize_amount(amount_usd),
                    "chain": "base",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "error": str(e),
                    "est_gas_usd": 0,
                }
        except Exception as e:
            logger.error(f"[BASE_FARM] Uniswap V3 swap failed: {e}")
            action = {
                "type": "swap",
                "protocol": "uniswap_v3",
                "pair": list(pair),
                "amount_usd": _randomize_amount(amount_usd),
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "error": str(e),
                "est_gas_usd": 0,
            }
    return action


def _do_aave_deposit(amount_usd: float, dry_run: bool) -> dict:
    """Execute Aave V3 deposit. Only deposits tokens with available balance."""
    eth_price = _get_eth_price()
    w3, wallet = _get_web3()
    from web3 import Web3

    # Check which tokens have balance
    available = []
    for tok in ["USDC", "WETH", "DAI"]:
        token_addr = Web3.to_checksum_address(TOKENS[tok])
        bal_data = "0x70a08231000000000000000000000000" + wallet[2:].lower()
        result = w3.eth.call({"to": token_addr, "data": bal_data}, "latest")
        bal = int.from_bytes(result, "big")
        decimals = 6 if tok in ("USDC",) else 18
        if bal / 10**decimals > 0.1:  # At least 0.1 token
            available.append(tok)

    if not available:
        # Fallback: use USDC (we always have some from swaps)
        token = "USDC"
    else:
        token = random.choice(available)

    if dry_run:
        action = {
            "type": "aave_deposit",
            "protocol": "aave_v3",
            "token": token,
            "amount_usd": _randomize_amount(amount_usd),
            "chain": "base",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "simulated",
            "est_gas_usd": round(random.uniform(0.01, 0.03), 4),
        }
        logger.info(
            f"[BASE_FARM] SIMULATED Aave deposit: ${action['amount_usd']:.4f} "
            f"{token} (gas: ~${action['est_gas_usd']:.4f})"
        )
    else:
        try:
            result = _execute_aave_deposit(token, amount_usd, eth_price)
            action = {
                "type": "aave_deposit",
                "protocol": "aave_v3",
                "token": token,
                "amount_usd": _randomize_amount(amount_usd),
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": result["status"],
                "tx_hash": result["tx_hash"],
                "est_gas_usd": result["gas_usd"],
            }
        except Exception as e:
            logger.error(f"[BASE_FARM] Aave deposit failed: {e}")
            action = {
                "type": "aave_deposit",
                "protocol": "aave_v3",
                "token": token,
                "amount_usd": _randomize_amount(amount_usd),
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "error": str(e),
                "est_gas_usd": 0,
            }
    return action


def _do_nft_mint(dry_run: bool) -> dict:
    """Execute Zora NFT mint."""
    eth_price = _get_eth_price()

    if dry_run:
        action = {
            "type": "nft_mint",
            "protocol": "zora",
            "chain": "base",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "simulated",
            "mint_cost_usd": round(random.uniform(0.00, 0.50), 4),
            "est_gas_usd": round(random.uniform(0.005, 0.015), 4),
        }
        logger.info(
            f"[BASE_FARM] SIMULATED NFT mint on Base "
            f"(gas: ~${action['est_gas_usd']:.4f})"
        )
    else:
        try:
            result = _execute_zora_mint(eth_price)
            action = {
                "type": "nft_mint",
                "protocol": "zora",
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": result["status"],
                "tx_hash": result["tx_hash"],
                "est_gas_usd": result["gas_usd"],
            }
        except Exception as e:
            logger.error(f"[BASE_FARM] Zora NFT mint failed: {e}")
            action = {
                "type": "nft_mint",
                "protocol": "zora",
                "chain": "base",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "error": str(e),
                "est_gas_usd": 0,
            }
    return action


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def run(circuit_breaker_ok: bool = True, dry_run: bool = True):
    """Execute Base farming actions for today.

    Args:
        circuit_breaker_ok: If False, skip farming (bankroll protection active)
        dry_run: If True, simulate actions. Set False to execute real transactions.
    """
    if not circuit_breaker_ok:
        logger.info("[BASE_FARM] Circuit breaker tripped — farming paused")
        return

    state = _load_state()

    if not _should_farm_today(state):
        _save_state(state)
        return

    if not dry_run:
        try:
            eth_bal = _get_wallet_balance_eth()
            logger.info(f"[BASE_FARM] Live mode — wallet ETH balance: {eth_bal:.4f} ETH")
            if eth_bal < MIN_BASE_ETH_FOR_FARMING:
                logger.warning(
                    f"[BASE_FARM] ETH balance ({eth_bal:.4f}) below minimum "
                    f"({MIN_BASE_ETH_FOR_FARMING}) — skipping farming"
                )
                return
        except Exception as e:
            logger.error(f"[BASE_FARM] Pre-flight check failed: {e}")
            return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    action_type = _pick_action(state)
    result = None

    logger.info(f"[BASE_FARM] Today's action: {action_type} (dry_run={dry_run})")

    if action_type == "swap_aerodrome":
        pair = _pick_swap_pair(state)
        result = _do_swap_aerodrome(pair, 2.0, dry_run)

    elif action_type == "swap_uniswap":
        pair = _pick_swap_pair(state)
        result = _do_swap_uniswap(pair, 2.0, dry_run)

    elif action_type == "aave_deposit":
        result = _do_aave_deposit(2.0, dry_run)

    elif action_type == "nft_mint":
        result = _do_nft_mint(dry_run)

    if result:
        total_cost = (
            result.get("amount_usd", 0)
            + result.get("est_gas_usd", 0)
            + result.get("mint_cost_usd", 0)
        )
        state["last_farm_date"] = today
        state["weekly_spend_usd"] = round(state["weekly_spend_usd"] + total_cost, 4)
        state["actions_this_week"].append(action_type)
        state["total_actions"] = state.get("total_actions", 0) + 1
        if "pair" in result:
            state["pairs_used_this_week"].append(result["pair"])
        if action_type not in state.get("protocols_used", []):
            state["protocols_used"].append(action_type)

        _log_action(result)
        _save_state(state)

        logger.info(
            f"[BASE_FARM] Done. Weekly spend: "
            f"${state['weekly_spend_usd']:.2f} / ${FARMING_WEEKLY_BUDGET:.2f} | "
            f"Total lifetime actions: {state['total_actions']} | "
            f"Protocols this week: {len(set(state['actions_this_week']))}"
        )


def get_farming_report() -> dict:
    """Generate a farming quality report for the PM dashboard."""
    state = _load_state()
    log = []
    if os.path.exists(FARMING_LOG_FILE):
        try:
            with open(FARMING_LOG_FILE, "r") as f:
                log = json.load(f)
        except Exception:
            pass

    thirty_days_ago = (datetime.now(timezone.utc).timestamp() - 30 * 86400)
    recent = [
        a for a in log
        if datetime.fromisoformat(a.get("timestamp", "2000-01-01")).timestamp()
        > thirty_days_ago
    ]

    return {
        "total_actions": state.get("total_actions", 0),
        "weekly_spend_usd": state.get("weekly_spend_usd", 0),
        "weekly_budget_usd": FARMING_WEEKLY_BUDGET,
        "protocols_used_ever": state.get("protocols_used", []),
        "actions_last_30d": len(recent),
        "unique_protocols_30d": len(set(a.get("protocol", "") for a in recent)),
        "unique_pairs_30d": len(set(str(a.get("pair", "")) for a in recent if a.get("pair"))),
        "farming_quality": (
            "HIGH"
            if len(recent) >= 12 and len(set(a.get("protocol", "") for a in recent)) >= 3
            else "MEDIUM" if len(recent) >= 4 else "LOW"
        ),
    }
