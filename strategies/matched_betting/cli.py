#!/usr/bin/env python3
"""
Matched Betting CLI — command-line interface for the bonus tracker and calculator.

Commands:
    python3 cli.py bonuses          — Show all sportsbooks and their bonus status
    python3 cli.py calc            — Run hedge calculator
    python3 cli.py record          — Record a bet
    python3 cli.py settle           — Settle a bet
    python3 cli.py summary          — Overall profit summary
    python3 cli.py rollover-ev      — Calculate EV of a rollover bonus
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from calculator import (
    american_to_decimal,
    decimal_to_implied_prob,
    calculate_hedge,
    find_optimal_odds_pair,
    calculate_rollover_ev,
)
from bonus_tracker import (
    init_db,
    seed_bonuses,
    get_all_sportsbooks,
    get_available_bonuses,
    get_active_bets,
    record_bet,
    settle_bet,
    record_conversion,
    get_total_profit,
    get_summary,
    update_sportsbook_status,
    add_sportsbook,
)


def cmd_bonuses(args):
    """Show all sportsbooks, their bonus status, and estimated EV."""
    init_db()
    seed_bonuses()
    books = get_all_sportsbooks()

    total_available = sum(b["signup_bonus_amount"] for b in books if b["status"] == "available")
    est_ev = total_available * 0.70

    print("\n=== Sportsbook Bonuses ===")
    print(f"  Available EV: ${est_ev:,.2f} (estimated @ 70% conversion)\n")
    print(f"  {'Sportsbook':<18} {'Type':<14} {'Amount':>9} {'Rollover':>8} {'Status':<12} {'EV':>9}")
    print(f"  {'-'*18} {'-'*14} {'-'*9} {'-'*8} {'-'*12} {'-'*9}")

    for b in books:
        status_color = {"available": "\033[92m", "claimed": "\033[93m",
                         "completed": "\033[94m", "expired": "\033[91m"}.get(b["status"], "")
        reset = "\033[0m"
        est_ev_bonus = b["signup_bonus_amount"] * 0.70 if b["status"] == "available" else 0
        print(
            f"  {b['name']:<18} {b['signup_bonus_type']:<14} "
            f"${b['signup_bonus_amount']:>8,.0f} {b['rollover_multiplier']:>7.0f}x "
            f"{status_color}{b['status']:<12}{reset} ${est_ev_bonus:>8,.0f}"
        )

    summary = get_summary()
    print(f"\n  Total bonuses: {summary['total_sportsbooks']} | "
          f"Available: {summary['available']} | "
          f"Claimed: {summary['claimed']} | "
          f"Completed: {summary['completed']}")
    print(f"  Total profit so far: ${summary['total_profit']:.2f}")
    print(f"  Avg conversion rate: {summary['avg_conversion_rate']:.1f}%")


def cmd_calc(args):
    """Run hedge calculator."""
    if args.back_odds is None or args.lay_odds is None or args.back_stake is None:
        print("Error: --back-stake, --back-odds, and --lay-odds are required")
        sys.exit(1)

    bonus_type = args.bonus_type or "free_bet"

    result = calculate_hedge(
        back_stake=args.back_stake,
        back_odds_american=args.back_odds,
        lay_odds_american=args.lay_odds,
        bonus_type=bonus_type,
    )

    print("\n=== Matched Betting Calculator ===")
    print(f"  Bonus type:    {bonus_type}")
    print(f"  Back stake:    ${args.back_stake:.2f} @ {args.back_odds:+d} (American)")
    print(f"  Back decimal:  {result['back_odds_decimal']:.4f}")
    print(f"  Lay odds:      {args.lay_odds:+d} (American)")
    print(f"  Lay decimal:   {result['lay_odds_decimal']:.4f}")
    print()
    print(f"  Lay stake:     ${result['lay_stake']:.2f}")
    print(f"  If back wins:  +${result['profit_if_back_wins']:.2f}")
    print(f"  If lay wins:   +${result['profit_if_lay_wins']:.2f}")
    print(f"  Guaranteed:    +${result['guaranteed_profit']:.2f}")
    print(f"  Conversion:   {result['conversion_rate']:.1f}%")

    if args.verbose:
        implied_back = decimal_to_implied_prob(result["back_odds_decimal"]) * 100
        implied_lay = decimal_to_implied_prob(result["lay_odds_decimal"]) * 100
        vig = implied_back + implied_lay - 100
        print(f"\n  Implied prob (back): {implied_back:.1f}%")
        print(f"  Implied prob (lay):   {implied_lay:.1f}%")
        print(f"  Total vig:            {vig:.1f}%")


def cmd_record(args):
    """Record a bet in the tracker."""
    init_db()
    seed_bonuses()

    # Find sportsbook id by name
    books = get_all_sportsbooks()
    book = next((b for b in books if b["name"].lower() == args.book.lower()), None)
    if not book:
        print(f"Error: Sportsbook '{args.book}' not found. Available: {[b['name'] for b in books]}")
        sys.exit(1)

    bet_id = record_bet(
        sportsbook_id=book["id"],
        bet_type=args.type,
        event=args.event,
        odds_american=args.odds,
        stake=args.stake,
        is_bonus_bet=args.bonus,
        hedge_book=args.hedge_book,
    )

    print(f"Recorded bet #{bet_id}: {args.type.upper()} ${args.stake} @ {args.odds:+d}")
    print(f"  Sportsbook: {book['name']}")
    print(f"  Event:      {args.event or 'N/A'}")
    print(f"  Bonus bet:  {'Yes' if args.bonus else 'No'}")
    print(f"  Hedge book: {args.hedge_book or 'N/A'}")

    # Optionally mark sportsbook as claimed
    if args.claim:
        update_sportsbook_status(book["id"], "claimed")
        print(f"  Status: marked as 'claimed'")


def cmd_settle(args):
    """Settle a bet."""
    init_db()
    status_map = {"w": "won", "l": "lost", "v": "void"}
    status = status_map.get(args.result.lower(), args.result)

    settle_bet(args.bet_id, status, args.pnl or 0.0)
    print(f"Bet #{args.bet_id} settled as '{status}' (PnL: ${args.pnl or 0:.2f})")


def cmd_summary(args):
    """Print overall summary."""
    init_db()
    seed_bonuses()
    summary = get_summary()

    print("\n=== Matched Betting Summary ===")
    print(f"  Sportsbooks:  {summary['total_sportsbooks']} total | "
          f"{summary['available']} available | "
          f"{summary['claimed']} claimed | "
          f"{summary['completed']} completed")
    print(f"  Bonus value:  ${summary['total_bonus_value']:,.0f} (available)")
    print(f"  Estimated EV: ${summary['estimated_ev']:,.2f} (assuming 70% conversion)")
    print(f"  Realized PnL: ${summary['total_profit']:.2f}")
    print(f"  Pending bets: {summary['pending_bets']}")
    print(f"  Avg conversion rate: {summary['avg_conversion_rate']:.1f}%")

    if summary["pending_bets"] > 0:
        active = get_active_bets()
        print(f"\n  Active bets:")
        for b in active:
            print(f"    #{b['id']}: {b['bet_type']} ${b['stake']} @ {b['odds_american']:+d} | "
                  f"status={b['status']}")


def cmd_rollover_ev(args):
    """Calculate EV of a rollover bonus."""
    result = calculate_rollover_ev(
        bonus_amount=args.bonus,
        rollover_multiplier=args.multiplier,
        avg_vig_pct=args.vig,
    )

    print("\n=== Rollover EV Calculator ===")
    print(f"  Bonus amount:         ${args.bonus:,.2f}")
    print(f"  Rollover multiplier:   {args.multiplier:.0f}x")
    print(f"  Total wagered:         ${result['total_wagered']:,.2f}")
    print(f"  Avg vig rate:          {args.vig:.1f}%")
    print(f"  Expected vig loss:     ${result['expected_loss_from_vig']:,.2f}")
    print(f"  Net EV:                ${result['net_ev']:,.2f}")
    verdict_color = {
        "WORTH_IT": "\033[92m",
        "NOT_WORTH_IT": "\033[91m",
        "BREAK_EVEN": "\033[93m",
    }.get(result["verdict"], "")
    reset = "\033[0m"
    print(f"  Verdict:              {verdict_color}{result['verdict']}{reset}")

    if result["verdict"] == "NOT_WORTH_IT":
        print(f"\n  This bonus is expected to LOSE money due to rollover requirements.")
        print(f"  Vig losses (${result['expected_loss_from_vig']:.2f}) exceed the bonus (${args.bonus:.2f}).")
    elif result["verdict"] == "WORTH_IT":
        print(f"\n  This bonus is profitable at 70%+ conversion rate.")


def cmd_add_sportsbook(args):
    """Add a new sportsbook to the tracker."""
    init_db()
    sid = add_sportsbook(
        name=args.name,
        bonus_type=args.bonus_type,
        bonus_amount=args.amount,
        rollover_multiplier=args.rollover,
        min_deposit=args.min_deposit,
        promo_code=args.promo_code,
        notes=args.notes,
    )
    print(f"Added sportsbook #{sid}: {args.name} — "
          f"${args.amount:.0f} {args.bonus_type} ({args.rollover:.0f}x rollover)")


def main():
    parser = argparse.ArgumentParser(description="Matched Betting CLI")
    sub = parser.add_subparsers(dest="cmd")

    # bonuses
    p_bonuses = sub.add_parser("bonuses", help="Show all sportsbook bonuses")

    # calc
    p_calc = sub.add_parser("calc", help="Run hedge calculator")
    p_calc.add_argument("--back-stake", type=float, help="Amount bet at sportsbook")
    p_calc.add_argument("--back-odds", type=int, help="American odds at sportsbook (e.g., +150)")
    p_calc.add_argument("--lay-odds", type=int, help="American odds at hedge book (e.g., -160)")
    p_calc.add_argument("--bonus-type", default="free_bet",
                        choices=["free_bet", "deposit_match", "risk_free"],
                        help="Type of bonus (default: free_bet)")
    p_calc.add_argument("--verbose", "-v", action="store_true", help="Show extra details")

    # record
    p_record = sub.add_parser("record", help="Record a bet")
    p_record.add_argument("--book", required=True, help="Sportsbook name")
    p_record.add_argument("--type", required=True, choices=["back", "lay"], help="Bet type")
    p_record.add_argument("--event", help="Event description")
    p_record.add_argument("--odds", type=int, help="American odds")
    p_record.add_argument("--stake", type=float, help="Stake amount")
    p_record.add_argument("--bonus", action="store_true", help="This is a bonus bet")
    p_record.add_argument("--hedge-book", help="Hedge sportsbook name")
    p_record.add_argument("--claim", action="store_true", help="Mark sportsbook as claimed")

    # settle
    p_settle = sub.add_parser("settle", help="Settle a bet")
    p_settle.add_argument("--bet-id", type=int, required=True, help="Bet ID to settle")
    p_settle.add_argument("--result", required=True, help="Result: w(on), l(ost), v(oid)")
    p_settle.add_argument("--pnl", type=float, default=0.0, help="Profit/loss")

    # summary
    sub.add_parser("summary", help="Overall profit summary")

    # rollover-ev
    p_re = sub.add_parser("rollover-ev", help="Calculate rollover EV")
    p_re.add_argument("--bonus", type=float, required=True, help="Bonus amount in dollars")
    p_re.add_argument("--multiplier", type=float, required=True, help="Rollover multiplier (e.g., 25)")
    p_re.add_argument("--vig", type=float, default=4.5, help="Average vig %% (default: 4.5)")

    # add-sportsbook
    p_add = sub.add_parser("add-sportsbook", help="Add a new sportsbook")
    p_add.add_argument("--name", required=True, help="Sportsbook name")
    p_add.add_argument("--bonus-type", required=True,
                       choices=["free_bet", "deposit_match", "risk_free"])
    p_add.add_argument("--amount", type=float, required=True, help="Bonus amount")
    p_add.add_argument("--rollover", type=float, default=1.0, help="Rollover multiplier")
    p_add.add_argument("--min-deposit", type=float, help="Minimum deposit")
    p_add.add_argument("--promo-code", help="Promo code")
    p_add.add_argument("--notes", help="Notes")

    args = parser.parse_args()

    if args.cmd == "bonuses":
        cmd_bonuses(args)
    elif args.cmd == "calc":
        cmd_calc(args)
    elif args.cmd == "record":
        cmd_record(args)
    elif args.cmd == "settle":
        cmd_settle(args)
    elif args.cmd == "summary":
        cmd_summary(args)
    elif args.cmd == "rollover-ev":
        cmd_rollover_ev(args)
    elif args.cmd == "add-sportsbook":
        cmd_add_sportsbook(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
