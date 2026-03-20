# Matched Betting System

Convert sportsbook signup bonuses into **guaranteed profit** by placing opposing bets across two books.

## What is Matched Betting?

Matched betting (also called bonus hunting or arb betting) exploits the fact that sportsbooks offer signup bonuses to new customers. By placing **two opposing bets** — one at the sportsbook with the bonus, and one at a hedge book on the opposite outcome — you lock in a guaranteed profit equal to the bonus value minus a small "hedge cost" (the vig/juice difference between the two books).

**Example:** A sportsbook offers a "Bet $100, get $200 in free bets" promotion. You bet $100 at the sportsbook on Team A to win. You then lay (bet against) Team A at a hedge book. Regardless of who wins, you end up with approximately $180-$190 in guaranteed cash after converting the free bet — turning the $200 bonus into real money.

**Key insight:** Sportsbooks rely on the law of large numbers — most customers won't complete the rollover and lose their bonus. By mathematically hedging every bonus, you eliminate the risk and capture nearly all the bonus value.

## How to Use This System

### Step 1: Claim a Bonus

Browse available bonuses with:
```bash
python3 strategies/matched_betting/cli.py bonuses
```

### Step 2: Calculate Your Hedge

Find the optimal hedge bet for your bonus:
```bash
python3 strategies/matched_betting/cli.py calc \
  --back-stake 500 --back-odds +150 \
  --lay-odds -160 --bonus-type free_bet
```

The calculator outputs:
- **Lay stake**: How much to bet at the hedge book
- **Guaranteed profit**: What you lock in regardless of outcome
- **Conversion rate**: Bonus % you actually capture (aim for 70%+)

### Step 3: Place Your Bets

1. **Back bet**: Place your bonus bet at the sportsbook
2. **Lay bet**: Bet the opposite outcome at a hedge book (Bet365, Pinnacle, etc.)
3. Use the lay stake from the calculator

### Step 4: Track Your Progress

```bash
# Record a bet
python3 strategies/matched_betting/cli.py record \
  --book DraftKings --type back \
  --event "NFL: Chiefs vs Bills" --odds +150 \
  --stake 100 --bonus --claim

# Settle after the event
python3 strategies/matched_betting/cli.py settle --bet-id 1 --result w --pnl 75.00

# Check your summary
python3 strategies/matched_betting/cli.py summary
```

### Step 5: Evaluate Rollover Requirements

Some bonuses require you to wager the bonus multiple times (rollover). Before claiming:
```bash
python3 strategies/matched_betting/cli.py rollover-ev \
  --bonus 1000 --multiplier 25 --vig 4.5
```

- **WORTH_IT**: Expected profit exceeds bonus cost
- **NOT_WORTH_IT**: Vig losses exceed the bonus value (DraftKings 25x is typically -$125)
- **BREAK_EVEN**: Margins too thin to matter

## Example: Converting a $500 FanDuel Risk-Free Bet

FanDuel offers a "risk-free first bet up to $500" (1x rollover on losses returned as site credit).

**Step 1:** Identify the offer
```bash
python3 strategies/matched_betting/cli.py bonuses
# FanDuel: risk_free, $1000, 1x rollover
```

**Step 2:** Calculate hedge
```bash
python3 strategies/matched_betting/cli.py calc \
  --back-stake 1000 --back-odds +110 \
  --lay-odds -115 --bonus-type risk_free
# Output: Lay stake: $954.55, Guaranteed: ~$857 (85.7% conversion)
```

**Step 3:** Place back bet at FanDuel ($1000 on +110 underdog)

**Step 4:** Place lay bet at Bet365 ($954.55 against the underdog at -115)

**Step 5:** Outcome
- **Underdog wins**: FanDuel pays $2100, lose $954.55 at Bet365 = **+$1145 profit** → convert site credit
- **Underdog loses**: Lose $1000 at FanDuel, Bet365 pays $954.55 = **-$45.45** (your hedge cost)
- Either way, you now have ~$857 in FanDuel site credit to convert

**Step 6:** Convert site credit at ~65-70%:
- Place $857 free bet at +150 odds → ~$500 guaranteed
- Total locked in: ~$500-$1145 depending on outcome

## Available Bonuses (Pre-Seeded)

| Sportsbook    | Bonus Type     | Amount  | Rollover | Est. EV |
|---------------|----------------|---------|----------|---------|
| DraftKings    | deposit_match  | $1,000  | 25x      | $700 ⚠️ |
| FanDuel       | risk_free      | $1,000  | 1x       | $700 ✓ |
| BetMGM        | deposit_match  | $1,500  | 1x       | $1,050 ✓|
| Caesars       | free_bet       | $1,000  | 1x       | $700 ✓ |
| PointsBet     | free_bet       | $500    | 1x       | $350 ✓ |
| BetRivers     | free_bet       | $500    | 1x       | $350 ✓ |
| Fanatics      | free_bet       | $1,000  | 1x       | $700 ✓ |
| ESPN BET      | risk_free      | $1,000  | 1x       | $700 ✓ |
| Hard Rock Bet | free_bet       | $100    | 1x       | $70 ✓ |
| Bet365        | free_bet       | $200    | 1x       | $140 ✓ |

⚠️ DraftKings 25x rollover is **NOT worth it** — expected loss of $125 due to vig.

## Warnings

- **Verify promo terms before depositing.** Rollover requirements, min odds, and eligible markets change frequently. This calculator uses estimates.
- **Sportsbooks may limit accounts.** Matched betting at scale can trigger gubbing (account restriction). Use VPN, vary bet sizes, and don't bet obvious arbs.
- **Taxes.** Gambling winnings may be taxable in your jurisdiction. Keep records.
- **This system is for entertainment and educational purposes.** Only bet what you can afford to lose.

## File Structure

```
strategies/matched_betting/
  __init__.py          — package marker
  calculator.py         — core math engine (odds conversion, hedge calc, rollover EV)
  bonus_tracker.py      — SQLite-backed tracker (sportsbooks, bets, conversions)
  cli.py                — command-line interface
README.md               — this file
```

## Database

SQLite at `paper_trading/bonuses.db`:
- `sportsbooks`: bonus offers from each book
- `bets`: individual back/lay bets
- `conversions`: completed bonus conversions with actual profit
