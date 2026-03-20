"""
Matched Betting Bonus Tracker — SQLite-backed tracker for sportsbook bonuses and bets.
DB: /opt/slimy/pm_updown_bot_bundle/paper_trading/bonuses.db
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "paper_trading", "bonuses.db")


def _get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sportsbooks (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            state TEXT,
            signup_bonus_type TEXT,
            signup_bonus_amount REAL,
            rollover_multiplier REAL DEFAULT 1.0,
            min_deposit REAL,
            promo_code TEXT,
            status TEXT DEFAULT 'available',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY,
            sportsbook_id INTEGER REFERENCES sportsbooks(id),
            bet_type TEXT NOT NULL,
            event TEXT,
            odds_american INTEGER,
            stake REAL,
            is_bonus_bet BOOLEAN DEFAULT 0,
            hedge_book TEXT,
            status TEXT DEFAULT 'pending',
            pnl REAL DEFAULT 0,
            placed_at TEXT DEFAULT (datetime('now')),
            settled_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversions (
            id INTEGER PRIMARY KEY,
            sportsbook_id INTEGER REFERENCES sportsbooks(id),
            bonus_amount REAL,
            conversion_rate REAL,
            guaranteed_profit REAL,
            actual_profit REAL,
            completed_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()


def add_sportsbook(
    name: str,
    bonus_type: str,
    bonus_amount: float,
    state: str = None,
    rollover_multiplier: float = 1.0,
    min_deposit: float = None,
    promo_code: str = None,
    notes: str = None,
) -> int:
    """Insert a new sportsbook. Returns its id."""
    conn = _get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO sportsbooks
                (name, state, signup_bonus_type, signup_bonus_amount,
                 rollover_multiplier, min_deposit, promo_code, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, state, bonus_type, bonus_amount, rollover_multiplier,
             min_deposit, promo_code, notes),
        )
        conn.commit()
        sportsbook_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        # Already exists — fetch id
        cursor.execute("SELECT id FROM sportsbooks WHERE name = ?", (name,))
        row = cursor.fetchone()
        sportsbook_id = row["id"]
    conn.close()
    return sportsbook_id


def seed_bonuses():
    """Pre-populate with major US sportsbooks."""
    bonuses = [
        ("DraftKings",    "deposit_match", 1000,   25.0, 5,   "MAXPROMO",   "Check rollover — DK has been 10-25x historically"),
        ("FanDuel",       "risk_free",     1000,    1.0, 10,  None,         "1x rollover on losses returned as site credit"),
        ("BetMGM",        "deposit_match", 1500,    1.0, 10,  "MAXBONUS",   "States: NJ, PA, CO, IA, AZ, etc."),
        ("Caesars",       "free_bet",      1000,    1.0, 20,  "MAXWINS",    "First bet only; free bet credited within 24h"),
        ("PointsBet",     "free_bet",       500,    1.0, 50,  "BET500",     "New customers only; $500 second-chance bet"),
        ("BetRivers",     "free_bet",       500,    1.0, 10,  "PLAY250",    "Match bonus up to $250 + $250 free bet"),
        ("Fanatics",      "free_bet",      1000,    1.0,  5,  None,         "Fanatics Sportsbook; new users"),
        ("ESPN BET",      "risk_free",     1000,    1.0, 10,  None,         "Formerly WynnBET; 1x rollover on losses"),
        ("Hard Rock Bet", "free_bet",       100,    1.0, 10,  None,         "New users; Florida and other states"),
        ("Bet365",        "free_bet",       200,    1.0, 10,  "MAXBETS",    "New customers; bet $1 get $200 in bets"),
    ]

    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sportsbooks")
    count = cursor.fetchone()[0]
    conn.close()

    if count > 0:
        return  # already seeded

    for name, bonus_type, amount, rollover, min_dep, promo, notes in bonuses:
        add_sportsbook(
            name=name,
            bonus_type=bonus_type,
            bonus_amount=amount,
            rollover_multiplier=rollover,
            min_deposit=min_dep,
            promo_code=promo,
            notes=notes,
        )


def get_available_bonuses() -> list:
    """List all unclaimed sportsbook bonuses."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM sportsbooks WHERE status = 'available' ORDER BY signup_bonus_amount DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_sportsbooks() -> list:
    """List all sportsbooks."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sportsbooks ORDER BY signup_bonus_amount DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_active_bets() -> list:
    """List pending bets."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bets WHERE status = 'pending'")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def record_bet(
    sportsbook_id: int,
    bet_type: str,
    event: str = None,
    odds_american: int = None,
    stake: float = 0.0,
    is_bonus_bet: bool = False,
    hedge_book: str = None,
) -> int:
    """Record a new bet. Returns bet id."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bets
            (sportsbook_id, bet_type, event, odds_american, stake,
             is_bonus_bet, hedge_book, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (sportsbook_id, bet_type, event, odds_american, stake,
         int(is_bonus_bet), hedge_book),
    )
    conn.commit()
    bet_id = cursor.lastrowid
    conn.close()
    return bet_id


def settle_bet(bet_id: int, status: str, pnl: float = 0.0) -> None:
    """Update bet status and PnL."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE bets
        SET status = ?, pnl = ?, settled_at = datetime('now')
        WHERE id = ?
        """,
        (status, pnl, bet_id),
    )
    conn.commit()
    conn.close()


def record_conversion(
    sportsbook_id: int,
    bonus_amount: float,
    conversion_rate: float,
    guaranteed_profit: float,
    actual_profit: float = None,
) -> int:
    """Record a completed bonus conversion."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO conversions
            (sportsbook_id, bonus_amount, conversion_rate, guaranteed_profit, actual_profit)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sportsbook_id, bonus_amount, conversion_rate,
         guaranteed_profit, actual_profit or guaranteed_profit),
    )
    conn.commit()
    conv_id = cursor.lastrowid
    conn.close()
    return conv_id


def get_total_profit() -> float:
    """Sum of all conversion actual_profit."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(SUM(actual_profit), 0) FROM conversions")
    total = cursor.fetchone()[0]
    conn.close()
    return float(total)


def get_total_guaranteed_profit() -> float:
    """Sum of all guaranteed_profit from available bonuses."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COALESCE(SUM(signup_bonus_amount * 0.70), 0) FROM sportsbooks WHERE status = 'available'"
    )
    # Estimate 70% average conversion for unclaimed bonuses
    total = cursor.fetchone()[0]
    conn.close()
    return float(total)


def get_summary() -> dict:
    """Overall summary of bonus tracking."""
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM sportsbooks WHERE status = 'available'")
    available = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sportsbooks WHERE status = 'claimed'")
    claimed = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sportsbooks WHERE status = 'completed'")
    completed = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sportsbooks")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(signup_bonus_amount), 0) FROM sportsbooks WHERE status = 'available'")
    total_bonus_value = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(actual_profit), 0) FROM conversions")
    total_profit = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM bets WHERE status = 'pending'")
    pending_bets = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COALESCE(AVG(conversion_rate), 0) FROM conversions WHERE conversion_rate > 0"
    )
    avg_conversion = cursor.fetchone()[0]

    conn.close()

    # Estimate 70% conversion rate for available bonuses
    estimated_ev = total_bonus_value * 0.70

    return {
        "total_sportsbooks": total,
        "available": available,
        "claimed": claimed,
        "completed": completed,
        "total_bonus_value": total_bonus_value,
        "estimated_ev": round(estimated_ev, 2),
        "total_profit": round(total_profit, 2),
        "pending_bets": pending_bets,
        "avg_conversion_rate": round(avg_conversion, 2),
    }


def update_sportsbook_status(sportsbook_id: int, status: str) -> None:
    """Update a sportsbook's status."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sportsbooks SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, sportsbook_id),
    )
    conn.commit()
    conn.close()
