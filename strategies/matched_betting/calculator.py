"""
Matched Betting Calculator — core math engine.
Converts sportsbook signup bonuses into guaranteed profit via hedge betting.
"""

import math


def american_to_decimal(american_odds: int) -> float:
    """Convert American odds to decimal odds.
    +150 → 2.50, -200 → 1.50"""
    if american_odds >= 0:
        return (american_odds / 100) + 1.0
    else:
        return (100 / abs(american_odds)) + 1.0


def decimal_to_implied_prob(decimal_odds: float) -> float:
    """Decimal odds to implied probability. 2.50 → 0.40"""
    return 1.0 / decimal_odds


def calculate_hedge(
    back_stake: float,
    back_odds_american: int,
    lay_odds_american: int,
    bonus_type: str = "free_bet",
) -> dict:
    """Calculate optimal hedge bet and guaranteed profit.

    Args:
        back_stake: Amount bet at sportsbook (with bonus)
        back_odds_american: Odds at sportsbook (American format)
        lay_odds_american: Odds at hedge book (American format, opposite side)
        bonus_type: "free_bet" | "deposit_match" | "risk_free"

    Returns:
        {
            "lay_stake": float,           # how much to bet at hedge book
            "profit_if_back_wins": float,
            "profit_if_lay_wins": float,
            "guaranteed_profit": float,    # min of the two profits
            "conversion_rate": float,      # guaranteed_profit / back_stake as percentage
            "back_odds_decimal": float,
            "lay_odds_decimal": float,
        }
    """
    back_odds_decimal = american_to_decimal(back_odds_american)
    lay_odds_decimal = american_to_decimal(lay_odds_american)

    if bonus_type == "free_bet":
        # Free bet: stake is not returned on win — only the profit is yours
        # Back wins: you receive (back_odds_decimal - 1) * back_stake as profit
        #            but your lay stake is lost: -(lay_odds_decimal - 1) * lay_stake
        # Lay wins: you receive lay_stake as profit, free bet stake lost = $0 cost
        #
        # Solve for lay_stake where both outcomes yield equal profit:
        # (back_odds_decimal - 1) * back_stake - (lay_odds_decimal - 1) * lay_stake = lay_stake
        # (back_odds_decimal - 2) * back_stake = lay_stake * lay_odds_decimal
        lay_stake = (back_odds_decimal - 2) * back_stake / lay_odds_decimal

        profit_if_back_wins = (back_odds_decimal - 1) * back_stake - (lay_odds_decimal - 1) * lay_stake
        profit_if_lay_wins = lay_stake

    elif bonus_type == "deposit_match":
        # Deposit match: bonus cash acts like real money (stake returned on win)
        # Back wins: back_stake * (back_odds_decimal - 1) - lay_stake * (lay_odds_decimal - 1)
        # Lay wins: lay_stake - back_stake (you get lay win but lose your back stake)
        #
        # Equal profit condition:
        # back_stake * (back_odds_decimal - 1) - (lay_odds_decimal - 1) * lay_stake = lay_stake - back_stake
        # back_stake * back_odds_decimal - lay_stake * (lay_odds_decimal - 1) = lay_stake
        # back_stake * back_odds_decimal = lay_stake * lay_odds_decimal
        lay_stake = back_stake * back_odds_decimal / lay_odds_decimal

        profit_if_back_wins = back_stake * (back_odds_decimal - 1) - lay_stake * (lay_odds_decimal - 1)
        profit_if_lay_wins = lay_stake - back_stake

    elif bonus_type == "risk_free":
        # Risk-free: two-step process.
        # Step 1: Place bet hoping to LOSE (so you get a free bet)
        # Step 2: If you win step 1, you already profited. If you lose, you get
        #         a free bet of the same amount — convert it using free_bet math.
        # For this calculator: assume worst case (lose step 1, convert free bet)
        # and compute the guaranteed profit using free_bet math.
        lay_stake = (back_odds_decimal - 2) * back_stake / lay_odds_decimal

        # Risk-free scenario where back loses: you get free bet back
        # Back loses (you get free bet): lay_stake profit - treat as free_bet conversion
        # But the free bet is the same stake, so profit_if_lay_wins is just lay_stake
        profit_if_back_wins = back_stake - lay_stake  # won at sportsbook, lose at lay book
        profit_if_lay_wins = lay_stake  # lost at sportsbook, won at lay book = free bet converted

    else:
        raise ValueError(f"Unknown bonus_type: {bonus_type!r}")

    guaranteed_profit = min(profit_if_back_wins, profit_if_lay_wins)
    conversion_rate = (guaranteed_profit / back_stake) * 100

    return {
        "lay_stake": round(lay_stake, 2),
        "profit_if_back_wins": round(profit_if_back_wins, 2),
        "profit_if_lay_wins": round(profit_if_lay_wins, 2),
        "guaranteed_profit": round(guaranteed_profit, 2),
        "conversion_rate": round(conversion_rate, 2),
        "back_odds_decimal": round(back_odds_decimal, 4),
        "lay_odds_decimal": round(lay_odds_decimal, 4),
    }


def find_optimal_odds_pair(
    target_profit: float,
    back_stake: float,
    bonus_type: str = "free_bet",
    min_odds: int = -300,
    max_odds: int = 300,
) -> dict:
    """Find the odds range where conversion rate is maximized.

    Args:
        target_profit: Desired guaranteed profit in dollars
        back_stake: Amount being wagered at sportsbook
        bonus_type: "free_bet" | "deposit_match" | "risk_free"
        min_odds: Minimum American odds to consider (e.g., -300)
        max_odds: Maximum American odds to consider (e.g., +300)

    Returns:
        dict with best back_odds, lay_odds, and conversion details
    """
    best = None
    best_conversion = -1

    for back_odds in range(min_odds, max_odds + 1, 5):
        if back_odds == 0:
            continue
        back_decimal = american_to_decimal(back_odds)
        # Lay odds should be opposite side and roughly symmetric
        # Best case: lay at same odds (no vig)
        for lay_odds in range(-max_odds, -min_odds + 1, 5):
            if lay_odds == 0:
                continue
            try:
                result = calculate_hedge(back_stake, back_odds, lay_odds, bonus_type)
                if result["conversion_rate"] > best_conversion:
                    best_conversion = result["conversion_rate"]
                    best = {
                        "back_odds": back_odds,
                        "lay_odds": lay_odds,
                        **result,
                    }
            except (ZeroDivisionError, ValueError):
                continue

    return best or {"error": "No valid odds pair found"}


def calculate_rollover_ev(
    bonus_amount: float,
    rollover_multiplier: float,
    avg_vig_pct: float = 4.5,
) -> dict:
    """Calculate EV of a rollover requirement.

    Args:
        bonus_amount: Dollar value of the bonus
        rollover_multiplier: e.g., 5x means bet 5 * bonus_amount
        avg_vig_pct: Typical sportsbook vig as percentage (default 4.5%)

    Returns:
        {
            "total_wagered": float,
            "expected_loss_from_vig": float,
            "net_ev": float,   # bonus_amount - expected_loss (positive = worth it)
            "verdict": str,     # "WORTH_IT" | "NOT_WORTH_IT" | "BREAK_EVEN"
        }
    """
    total_wagered = bonus_amount * rollover_multiplier
    expected_loss_from_vig = total_wagered * (avg_vig_pct / 100)
    net_ev = bonus_amount - expected_loss_from_vig

    if net_ev > 0.5:
        verdict = "WORTH_IT"
    elif net_ev < -0.5:
        verdict = "NOT_WORTH_IT"
    else:
        verdict = "BREAK_EVEN"

    return {
        "total_wagered": round(total_wagered, 2),
        "expected_loss_from_vig": round(expected_loss_from_vig, 2),
        "net_ev": round(net_ev, 2),
        "verdict": verdict,
    }
