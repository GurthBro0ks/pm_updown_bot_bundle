#!/usr/bin/env python3
"""
Airdrop Tracker with SQLite Persistence

Track user activity per protocol, store history in SQLite, and provide recommendations for next actions.
DB path: /opt/slimy/pm_updown_bot_bundle/paper_tracking/airdrop.db
"""

import sqlite3
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

# Database path
DB_PATH = os.environ.get("AIRDROP_DB_PATH", "/opt/slimy/pm_updown_bot_bundle/paper_trading/airdrop.db")

# Supported protocols and their recommended actions
# Status: CONFIRMED, EXPLORING, SEASON_X_LIVE, COMPLETED
COMPLETED_AIRDROPS = {"monad", "linea"}

PROTOCOLS = {
    "opensea": {
        "name": "OpenSea",
        "token": "$SEA",
        "status": "CONFIRMED",
        "completed": False,
        "actions": ["list_nft", "bid_nft", "connect_wallet", "view_collections"],
        "frequency_days": 7,
    },
    "aerodrome": {
        "name": "Aerodrome",
        "token": "TBD",
        "status": "EXPLORING",
        "completed": False,
        "actions": ["swap_tokens", "provide_liquidity", "stake_assets", "vote"],
        "frequency_days": 7,
    },
    "metamask": {
        "name": "MetaMask",
        "token": "$MASK",
        "status": "CONFIRMED",
        "completed": False,
        "actions": ["bridge_assets", "swap_tokens", "send_transaction", "interact_defi"],
        "frequency_days": 7,
    },
    "linea": {
        "name": "Linea",
        "token": "$LINEA",
        "status": "COMPLETED",
        "completed": True,
        "actions": ["deposit_eth", "use_bridge", "swap_tokens", "interact_contracts"],
        "frequency_days": 7,
    },
    "stargate": {
        "name": "Stargate",
        "token": "TBD",
        "status": "SEASON_2_LIVE",
        "completed": False,
        "actions": ["bridge_tokens", "stake_stg", "provide_liquidity", "vote"],
        "frequency_days": 7,
    },
    "monad": {
        "name": "Monad",
        "token": "$MON",
        "status": "COMPLETED",
        "completed": True,
        "actions": ["mainnet_bridge", "deploy_contract", "swap_tokens", "delegate_stake"],
        "frequency_days": 7,
    },
    "polymarket": {
        "name": "Polymarket",
        "token": "$POLY",
        "status": "CONFIRMED",
        "completed": False,
        "actions": ["place_bet", "create_market", "trade_nfts", "claim_fees"],
        "frequency_days": 3,
    },
    "base": {
        "name": "Base",
        "token": "TBD",
        "status": "EXPLORING",
        "completed": False,
        "actions": ["swap_tokens", "provide_liquidity", "use_bridge", "interact_defi", "mint_nft"],
        "frequency_days": 7,
    },
    "ethena": {
        "name": "Ethena",
        "token": "$ENA",
        "status": "SEASON_5_LIVE",
        "completed": False,
        "actions": ["stake_eth", "use_perps", "provide_liquidity"],
        "frequency_days": 7,
    },
}


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
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                protocol TEXT NOT NULL,
                action TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT (datetime('now')),
                notes TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_protocol_completed
            ON activities(protocol, completed_at)
        """)

        conn.commit()
    finally:
        conn.close()


def log_activity(protocol: str, action: str, notes: str = None) -> int:
    """
    Log a completed activity.

    Args:
        protocol: Protocol key (e.g., 'opensea', 'aerodrome')
        action: Action completed (e.g., 'swap_tokens', 'place_bet')
        notes: Optional notes about the activity

    Returns:
        Activity ID
    """
    if protocol not in PROTOCOLS:
        raise ValueError(f"Unknown protocol: {protocol}")

    # Warn if user is logging activity on completed airdrops
    if protocol in COMPLETED_AIRDROPS:
        import sys
        import logging
        logging.getLogger(__name__).warning(
            f"WARNING: {PROTOCOLS[protocol]['name']} airdrop is COMPLETED. "
            f"Do not waste gas farming this protocol. "
            f"Status: {PROTOCOLS[protocol]['status']}"
        )
        if notes:
            notes = f"[WARNING: Airdrop completed] {notes}"
        else:
            notes = f"[WARNING: Airdrop completed - {PROTOCOLS[protocol]['status']}]"

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO activities (protocol, action, completed_at, notes)
            VALUES (?, ?, datetime('now'), ?)
            """,
            (protocol, action, notes)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_protocol_status(protocol: str) -> Dict[str, Any]:
    """
    Get status for a single protocol.

    Args:
        protocol: Protocol key

    Returns:
        Dict with protocol status
    """
    if protocol not in PROTOCOLS:
        raise ValueError(f"Unknown protocol: {protocol}")

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            SELECT action, MAX(completed_at) as last_completed
            FROM activities
            WHERE protocol = ?
            GROUP BY action
            """,
            (protocol,)
        )
        rows = cursor.fetchall()

        activities = {row["action"]: row["last_completed"] for row in rows}

        cursor = conn.execute(
            """
            SELECT COUNT(*) as total, MAX(completed_at) as last_activity
            FROM activities
            WHERE protocol = ?
            """,
            (protocol,)
        )
        row = cursor.fetchone()

        return {
            "protocol": protocol,
            "name": PROTOCOLS[protocol]["name"],
            "total_activities": row["total"],
            "last_activity": row["last_activity"],
            "activities": activities,
            "recommended_actions": PROTOCOLS[protocol]["actions"],
            "frequency_days": PROTOCOLS[protocol]["frequency_days"],
        }
    finally:
        conn.close()


def get_dashboard() -> List[Dict[str, Any]]:
    """
    Get status for all protocols.

    Returns:
        List of protocol statuses
    """
    return [get_protocol_status(p) for p in PROTOCOLS]


def get_weekly_todo() -> List[Dict[str, Any]]:
    """
    Get overdue actions based on protocol frequency.

    Returns:
        List of recommended actions sorted by priority
    """
    conn = get_db()
    try:
        # Get all activities from the last 30 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        cursor = conn.execute(
            """
            SELECT protocol, MAX(completed_at) as last_activity
            FROM activities
            WHERE completed_at >= ?
            GROUP BY protocol
            """,
            (cutoff,)
        )
        rows = cursor.fetchall()

        last_activity_map = {row["protocol"]: row["last_activity"] for row in rows}

        todos = []
        for protocol, info in PROTOCOLS.items():
            last_activity = last_activity_map.get(protocol)
            frequency = info["frequency_days"]

            if last_activity:
                # Parse as naive datetime since SQLite stores without timezone
                last_dt = datetime.strptime(last_activity.split('.')[0], "%Y-%m-%d %H:%M:%S")
                now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                days_since = (now_dt - last_dt).days
                overdue = days_since - frequency
            else:
                # Never done - highest priority
                overdue = 100

            # Determine if action is needed
            if overdue > 0:
                # Find the oldest action that hasn't been done recently
                done_actions = set()
                cursor = conn.execute(
                    """
                    SELECT action FROM activities
                    WHERE protocol = ? AND completed_at >= ?
                    """,
                    (protocol, cutoff)
                )
                for row in cursor:
                    done_actions.add(row["action"])

                # Get next action to do
                next_action = None
                for action in info["actions"]:
                    if action not in done_actions:
                        next_action = action
                        break

                if next_action is None:
                    next_action = info["actions"][0]  # Loop back to first

                todos.append({
                    "protocol": protocol,
                    "name": info["name"],
                    "action": next_action,
                    "priority": "high" if overdue > 14 else ("medium" if overdue > 7 else "low"),
                    "days_overdue": overdue,
                    "last_activity": last_activity,
                })

        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        todos.sort(key=lambda x: (priority_order[x["priority"]], -x["days_overdue"]))

        return todos
    finally:
        conn.close()


# Initialize database on module import
init_db()
