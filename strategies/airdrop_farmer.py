#!/usr/bin/env python3
"""
Airdrop Farming Automation Module - Phase 4
Executes weekly on-chain interactions to qualify for token airdrops.
All interactions are SPOT ONLY (no derivatives, no leverage).

Legal status: ✅ Michigan-legal (spot DeFi interactions)
Tax: Airdropped tokens are taxable as ordinary income at FMV on receipt

NOTE: This module runs in TRACKER MODE - logs reminders for manual actions.
      Actual on-chain transactions require wallet integration (Phase 2).

Configuration centralized in config.py
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Import centralized config
from config import RISK_CAPS

# Setup logging
LOG_DIR = Path("/opt/slimy/pm_updown_bot_bundle/logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('/opt/slimy/pm_updown_bot_bundle/logs/airdrop_farmer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Gas budget cap
MAX_MONTHLY_GAS_USD = 50.0

# Target protocols and actions (weekly/monthly frequency)
# Priority tiers: S (confirmed), A (exploring), B (pending), F (failed/completed)
WEEKLY_ACTIONS = [
    {
        "name": "Polymarket Trading",
        "chain": "polymarket",
        "action": "place_bet",
        "protocol": "polymarket",
        "amount_usd": 5,
        "frequency": "weekly",
        "airdrop_target": "$POLY (CONFIRMED)",
        "priority": "S",
    },
    {
        "name": "Base: Aerodrome Swap",
        "chain": "base",
        "action": "swap_small_amount",
        "protocol": "aerodrome",
        "amount_usd": 5,
        "frequency": "weekly",
        "airdrop_target": "Base token + Aerodrome (#1 PRIORITY)",
        "priority": "S",
    },
    {
        "name": "MetaMask: Portfolio Swap",
        "chain": "ethereum",
        "action": "metamask_swap",
        "amount_usd": 5,
        "frequency": "weekly",
        "airdrop_target": "$MASK (CONFIRMED)",
        "priority": "S",
    },
]

MONTHLY_ACTIONS = [
    {
        "name": "Stargate Bridge",
        "chain": "multi",
        "action": "bridge_small_amount",
        "protocol": "stargate",
        "amount_usd": 10,
        "frequency": "monthly",
        "airdrop_target": "LayerZero S2",
    },
]

# Testnets (FREE — no capital needed)
# NOTE: Monad testnet is COMPLETED - users EXCLUDED from airdrop, DO NOT FARM
TESTNET_ACTIONS = [
    {"name": "MegaETH Testnet", "chain": "testnet", "frequency": "weekly", "warning": "Check if still active"},
    {"name": "Aztec Sandbox", "chain": "testnet", "action": "run_sandbox", "frequency": "monthly"},
]

# All actions combined
ALL_ACTIONS = WEEKLY_ACTIONS + MONTHLY_ACTIONS + TESTNET_ACTIONS

def get_airdrop_status() -> list:
    """
    Get airdrop status sorted by tier (S first, then A, then F).

    Returns:
        List of dicts with: protocol, token, status, tier, est_value, action, note
    """
    tier_order = {"S": 0, "A": 1, "F": 2, "B": 3}

    results = []
    for protocol, target in AIRDROP_TARGETS.items():
        results.append({
            "protocol": protocol,
            "token": target.get("token", "TBD"),
            "status": target.get("status", "UNKNOWN"),
            "tier": target.get("tier", "B"),
            "est_value": target.get("est_value", "unknown"),
            "action": target.get("action", ""),
            "note": target.get("name", ""),
        })

    # Sort by tier: S first, then A, then F
    results.sort(key=lambda x: (tier_order.get(x["tier"], 3), x["protocol"]))

    return results


# Airdrop targets with TGE timeline (2026)
# Priority: S (confirmed), A (exploring), B (pending), F (failed/completed)
AIRDROP_TARGETS = {
    "opensea": {
        "name": "OpenSea ($SEA)",
        "token": "$SEA",
        "tge": "Q1 2026",
        "status": "CONFIRMED",
        "confirmed": True,
        "completed": False,
        "tier": "S",
        "action": "URGENT: TGE launched Q1 2026. 50% to community. Use OS2, swap tokens, earn XP, claim NOW",
        "est_value": "$100-$2,000+"
    },
    "polymarket": {
        "name": "Polymarket ($POLY)",
        "token": "$POLY",
        "tge": "Q1-Q2 2026",
        "status": "CONFIRMED",
        "confirmed": True,
        "completed": False,
        "tier": "S",
        "action": "SNAPSHOT NOT YET TAKEN - FARM NOW. Trade prediction markets, create markets, claim fees",
        "est_value": "$200-$5,000+"
    },
    "metamask": {
        "name": "MetaMask ($MASK)",
        "token": "$MASK",
        "tge": "2026",
        "status": "CONFIRMED",
        "confirmed": True,
        "completed": False,
        "tier": "S",
        "action": "CONFIRMED 'sooner than expected'. Use MetaMask Swaps, Bridge, Portfolio weekly",
        "est_value": "$100-$3,000+"
    },
    "base": {
        "name": "Base (Coinbase L2)",
        "token": "TBD",
        "tge": "Q2-Q4 2026",
        "status": "EXPLORING",
        "confirmed": False,
        "completed": False,
        "tier": "S",
        "action": "#1 PRIORITY. NO TOKEN YET but exploring Q2-Q4 2026. Weekly swaps on Aerodrome, use dApps",
        "est_value": "unknown (potential huge)"
    },
    "monad": {
        "name": "Monad ($MON)",
        "token": "$MON",
        "tge": "Oct 2025 (COMPLETED)",
        "status": "COMPLETED",
        "confirmed": True,
        "completed": True,
        "tier": "F",
        "action": "AIRDROP COMPLETE Oct 2025. Testnet users EXCLUDED. Do NOT waste gas on testnet. Farm ecosystem: Ambient, Magic Eden, aPriori",
        "est_value": "Already distributed - too late"
    },
    "layerzero": {
        "name": "LayerZero S2",
        "token": "TBD",
        "tge": "2026",
        "status": "SEASON_2_LIVE",
        "confirmed": True,
        "completed": False,
        "tier": "A",
        "action": "Season 2 LIVE. Bridge via Stargate across chains weekly",
        "est_value": "unknown"
    },
    "linea": {
        "name": "Linea ($LINEA)",
        "token": "$LINEA",
        "tge": "Sep 2025 (COMPLETED)",
        "status": "COMPLETED",
        "confirmed": True,
        "completed": True,
        "tier": "F",
        "action": "AIRDROP COMPLETE Sep 2025. Claim closed Dec 9. Do NOT waste gas farming Linea.",
        "est_value": "Already distributed - too late"
    },
    "ethena": {
        "name": "Ethena ($ENA)",
        "token": "$ENA",
        "tge": "2026",
        "status": "SEASON_5_LIVE",
        "confirmed": True,
        "completed": False,
        "tier": "A",
        "action": "Season 5 LIVE. Repeat distributor. Use Ethena for restaking/perp.",
        "est_value": "unknown"
    },
}

# State file
AIRDROP_STATE_FILE = Path("/opt/slimy/pm_updown_bot_bundle/paper_trading/airdrop_state.json")


def load_airdrop_state() -> dict:
    """Load airdrop farming state from JSON"""
    if AIRDROP_STATE_FILE.exists():
        try:
            with open(AIRDROP_STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load airdrop state: {e}")

    return {
        "last_actions": {},
        "total_gas_spent_usd": 0.0,
        "last_updated": None
    }


def save_airdrop_state(state: dict):
    """Save airdrop farming state to JSON"""
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    AIRDROP_STATE_FILE.parent.mkdir(exist_ok=True)
    with open(AIRDROP_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def get_due_actions() -> list:
    """Return actions due based on their frequency"""
    state = load_airdrop_state()
    last_actions = state.get("last_actions", {})
    now = datetime.now(timezone.utc)

    due = []

    for action in ALL_ACTIONS:
        action_name = action["name"]
        frequency = action["frequency"]

        # Get last execution time
        last_exec_iso = last_actions.get(action_name)
        if last_exec_iso:
            try:
                last_exec = datetime.fromisoformat(last_exec_iso.replace('Z', '+00:00'))
            except:
                last_exec = None
        else:
            last_exec = None

        # Calculate days since last execution
        if last_exec:
            days_since = (now - last_exec).days
        else:
            days_since = float('inf')  # Never executed

        # Check if due
        is_due = False
        if frequency == "weekly" and (days_since >= 7 or last_exec is None):
            is_due = True
        elif frequency == "monthly" and (days_since >= 30 or last_exec is None):
            is_due = True

        if is_due:
            due.append({
                **action,
                "days_since_last": days_since if last_exec else "never",
                "recommended": True
            })

    return due


def log_airdrop_status():
    """Log current airdrop farming status and upcoming TGEs"""
    logger.info("=" * 60)
    logger.info("[AIRDROP] === Weekly Airdrop Farming Status ===")
    logger.info("=" * 60)

    # Tier S - Confirmed TGE (highest priority)
    logger.info("[AIRDROP] TIER S - CONFIRMED AIRDROP (Farm NOW):")
    for key in ["opensea", "polymarket", "metamask", "base"]:
        target = AIRDROP_TARGETS[key]
        if not target.get("completed", False):
            logger.info(f"  {target['name']}: TGE {target['tge']} | Status: {target['status']}")
            logger.info(f"    Action: {target['action']}")
            logger.info(f"    Est. Value: {target['est_value']}")

    # Tier A - Season/Exploring
    logger.info("[AIRDROP] TIER A - SEASON LIVE / EXPLORING:")
    for key in ["layerzero", "ethena"]:
        target = AIRDROP_TARGETS[key]
        logger.info(f"  {target['name']}: TGE {target['tge']} | Status: {target['status']}")
        logger.info(f"    Action: {target['action']}")

    # Tier F - Failed/Completed (WARNING - do not farm)
    logger.info("[AIRDROP] TIER F - COMPLETED/EXCLUDED (DO NOT FARM):")
    for key in ["monad", "linea"]:
        target = AIRDROP_TARGETS[key]
        logger.info(f"  {target['name']}: {target['status']}")
        logger.info(f"    WARNING: {target['action']}")

    # Gas budget
    state = load_airdrop_state()
    total_gas = state.get("total_gas_spent_usd", 0.0)
    logger.info(f"[AIRDROP] Gas Budget: ${total_gas:.2f}/$50/month cap")

    logger.info("=" * 60)


def log_due_actions(actions: list):
    """Log which actions are due this week/month"""
    if not actions:
        logger.info("[AIRDROP] No actions due this period")
        return

    logger.info("=" * 60)
    logger.info(f"[AIRDROP] {len(actions)} ACTIONS DUE:")
    logger.info("=" * 60)

    for action in actions:
        logger.info(f"[AIRDROP] DUE: {action['name']}")
        logger.info(f"  Chain: {action['chain']} | Amount: ${action.get('amount_usd', 'N/A')}")
        logger.info(f"  Target: {action.get('airdrop_target', 'N/A')}")
        logger.info(f"  Last executed: {action.get('days_since_last', 'never')}")

    logger.info("=" * 60)
    logger.info("[AIRDROP] REMINDER: Execute these actions manually or configure wallet for automation")
    logger.info(f"[AIRDROP] Estimated gas cost: ${sum(a.get('amount_usd', 0) for a in actions):.2f}")


def mark_action_done(action_name: str):
    """Mark an action as completed"""
    state = load_airdrop_state()
    state["last_actions"][action_name] = datetime.now(timezone.utc).isoformat()

    # Update gas spent
    for action in ALL_ACTIONS:
        if action["name"] == action_name:
            gas_cost = action.get("amount_usd", 0)
            state["total_gas_spent_usd"] = state.get("total_gas_spent_usd", 0.0) + gas_cost
            break

    save_airdrop_state(state)
    logger.info(f"[AIRDROP] Marked '{action_name}' as completed")


def generate_airdrop_proof(due_actions: list, airdrop_targets: dict):
    """Generate proof of airdrop farming status"""
    from utils.proof import generate_proof

    proof_id = f"airdrop_farming_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": "tracker",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "due_actions_count": len(due_actions),
        "due_actions": due_actions,
        "airdrop_targets": {k: {"name": v["name"], "tge": v["tge"], "status": v["status"]}
                           for k, v in airdrop_targets.items()},
        "gas_budget": {
            "spent": load_airdrop_state().get("total_gas_spent_usd", 0.0),
            "max": MAX_MONTHLY_GAS_USD
        },
        "legal_note": "Michigan-legal: spot DeFi interactions only, no derivatives"
    }

    generate_proof(proof_id, proof_data)
    logger.info(f"[AIRDROP] Proof: {proof_id}")
    return proof_id


def main(mode="shadow", verbose=False):
    """Phase 4: Airdrop Farming Automation (TRACKER MODE)"""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("PHASE 4: AIRDROP FARMING AUTOMATION (TRACKER MODE)")
    logger.info("Legal: Spot DeFi interactions only - no derivatives")
    logger.info("=" * 60)

    # Log airdrop status
    log_airdrop_status()

    # Get due actions
    due = get_due_actions()

    # Log due actions
    log_due_actions(due)

    # Generate proof
    generate_airdrop_proof(due, AIRDROP_TARGETS)

    logger.info(f"[AIRDROP] Exit code: {len(due)}")
    return len(due)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Airdrop Farming Automation - Phase 4")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow", help="Execution mode")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    sys.exit(main(mode=args.mode, verbose=args.verbose))
