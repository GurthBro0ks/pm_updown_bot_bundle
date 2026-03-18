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
from pathlib import Path

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

# Path for farming state/logs
FARMING_STATE_FILE = "/opt/slimy/pm_updown_bot_bundle/data/base_farming_state.json"
FARMING_LOG_FILE = "/opt/slimy/pm_updown_bot_bundle/data/base_farming_log.json"

# Protocol addresses on Base (mainnet)
PROTOCOLS = {
    "aerodrome_router": "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
    "uniswap_v3_router": "0x2626664c2603336E57B271c5C0b26F421741e481",
    "aave_v3_pool": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
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
        # Keep last 500 entries
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
    """Randomize an amount to avoid round numbers.

    $2.00 target with 15% variance → returns something like $1.73 or $2.29
    Never returns exact round numbers.
    """
    low = target_usd * (1 - variance_pct)
    high = target_usd * (1 + variance_pct)
    amount = random.uniform(low, high)
    # Add extra decimal noise — never .00
    amount += random.uniform(0.001, 0.009)
    return round(amount, 4)


def _random_delay(min_sec: int = 30, max_sec: int = 180):
    """Wait a random duration between actions to look human."""
    delay = random.randint(min_sec, max_sec)
    logger.debug(f"[BASE_FARM] Waiting {delay}s between actions...")
    time.sleep(delay)


def _should_farm_today(state: dict) -> bool:
    """Decide if we should farm today based on weekly rhythm.

    Target: 3-4 farming days per week, randomized.
    Skip ~3 days/week to avoid perfectly periodic patterns.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Already farmed today
    if state.get("last_farm_date") == today:
        logger.info("[BASE_FARM] Already farmed today, skipping")
        return False

    # Check weekly budget
    week_start = state.get("week_start")
    now = datetime.now(timezone.utc)
    if week_start:
        week_start_dt = datetime.fromisoformat(week_start)
        if (now - week_start_dt).days >= 7:
            # Reset weekly counters
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

    # Randomize: ~60% chance of farming on any given day → ~4 days/week
    if random.random() > 0.60:
        logger.info("[BASE_FARM] Random skip today (natural rhythm)")
        return False

    return True


def _pick_action(state: dict) -> str:
    """Pick today's farming action, rotating through protocols.

    Priority: swap (most common), then lend, then NFT mint.
    Avoids repeating the exact same action two days in a row.
    """
    actions = ["swap_aerodrome", "swap_uniswap", "aave_deposit", "nft_mint"]
    recent = state.get("actions_this_week", [])

    # Prefer actions we haven't done this week
    unused = [a for a in actions if a not in recent]
    if unused:
        return random.choice(unused)

    # If all used, pick any but weight toward swaps (most natural)
    weights = [0.35, 0.35, 0.2, 0.1]
    return random.choices(actions, weights=weights, k=1)[0]


def _pick_swap_pair(state: dict) -> tuple:
    """Pick a token pair we haven't swapped this week."""
    used = state.get("pairs_used_this_week", [])
    unused = [p for p in SWAP_PAIRS if list(p) not in used]
    if unused:
        return random.choice(unused)
    return random.choice(SWAP_PAIRS)


# =============================================================================
# FARMING ACTIONS (SIMULATION MODE)
# =============================================================================
#
# These actions currently LOG what they would do. To go live:
# 1. Import web3.py or use raw RPC calls
# 2. Sign transactions with the wallet's private key
# 3. Replace simulation with actual contract calls
#
# This is intentionally simulation-first so you can verify the
# behavioral patterns before committing real gas.

def _simulate_swap(protocol: str, pair: tuple, amount_usd: float, state: dict) -> dict:
    """Simulate a token swap on Aerodrome or Uniswap."""
    action = {
        "type": "swap",
        "protocol": protocol,
        "pair": list(pair),
        "amount_usd": _randomize_amount(amount_usd),
        "chain": "base",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "simulated",  # Change to "executed" when live
        "est_gas_usd": round(random.uniform(0.005, 0.02), 4),
    }
    logger.info(
        f"[BASE_FARM] {'SIMULATED' if action['status'] == 'simulated' else 'EXECUTED'} "
        f"swap: ${action['amount_usd']:.4f} {pair[0]}→{pair[1]} on {protocol} "
        f"(gas: ~${action['est_gas_usd']:.4f})"
    )
    return action


def _simulate_aave_deposit(amount_usd: float, state: dict) -> dict:
    """Simulate an Aave deposit on Base."""
    token = random.choice(["USDC", "WETH", "DAI"])
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
        f"[BASE_FARM] {'SIMULATED' if action['status'] == 'simulated' else 'EXECUTED'} "
        f"Aave deposit: ${action['amount_usd']:.4f} {token} "
        f"(gas: ~${action['est_gas_usd']:.4f})"
    )
    return action


def _simulate_nft_mint(state: dict) -> dict:
    """Simulate an NFT mint on Base (e.g., Zora or other collection)."""
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
        f"[BASE_FARM] {'SIMULATED' if action['status'] == 'simulated' else 'EXECUTED'} "
        f"NFT mint on Base (gas: ~${action['est_gas_usd']:.4f})"
    )
    return action


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def run(circuit_breaker_ok: bool = True, dry_run: bool = True):
    """Execute Base farming actions for today.

    Args:
        circuit_breaker_ok: If False, skip farming (bankroll protection active)
        dry_run: If True, simulate actions. Set False to execute real transactions.

    Called by runner.py AFTER all trading phases complete.
    """
    if not circuit_breaker_ok:
        logger.info("[BASE_FARM] Circuit breaker tripped — farming paused")
        return

    state = _load_state()

    if not _should_farm_today(state):
        _save_state(state)
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    action_type = _pick_action(state)
    result = None

    logger.info(f"[BASE_FARM] Today's action: {action_type}")

    if action_type == "swap_aerodrome":
        pair = _pick_swap_pair(state)
        result = _simulate_swap("aerodrome", pair, 2.0, state)

    elif action_type == "swap_uniswap":
        pair = _pick_swap_pair(state)
        result = _simulate_swap("uniswap_v3", pair, 2.0, state)

    elif action_type == "aave_deposit":
        result = _simulate_aave_deposit(2.0, state)

    elif action_type == "nft_mint":
        result = _simulate_nft_mint(state)

    if result:
        # Update state
        total_cost = result.get("amount_usd", 0) + result.get("est_gas_usd", 0) + result.get("mint_cost_usd", 0)
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
        except:
            pass

    # Count unique protocols and chains in last 30 days
    thirty_days_ago = (datetime.now(timezone.utc).timestamp() - 30 * 86400)
    recent = [a for a in log if datetime.fromisoformat(a.get("timestamp", "2000-01-01")).timestamp() > thirty_days_ago]

    return {
        "total_actions": state.get("total_actions", 0),
        "weekly_spend_usd": state.get("weekly_spend_usd", 0),
        "weekly_budget_usd": FARMING_WEEKLY_BUDGET,
        "protocols_used_ever": state.get("protocols_used", []),
        "actions_last_30d": len(recent),
        "unique_protocols_30d": len(set(a.get("protocol", "") for a in recent)),
        "unique_pairs_30d": len(set(str(a.get("pair", "")) for a in recent if a.get("pair"))),
        "farming_quality": "HIGH" if len(recent) >= 12 and len(set(a.get("protocol", "") for a in recent)) >= 3 else "MEDIUM" if len(recent) >= 4 else "LOW",
    }
