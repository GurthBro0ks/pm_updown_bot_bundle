# Competitor Analysis: ryanfrigo/kalshi-ai-trading-bot

**Source:** `https://github.com/ryanfrigo/kalshi-ai-trading-bot` (MIT licensed, 162 stars)
**Analysis date:** 2026-03-20
**Our bot:** pm_updown_bot_bundle

---

## Architecture Comparison

| Feature | Their Bot | Our Bot |
|---|---|---|
| **AI approach** | 5-model role-based parallel ensemble + structured Bull/Bear debate | MiniMax cascade (Grok → GLM → MiniMax), single probability estimate |
| **Model roster** | Grok-3, Claude 3.5 Sonnet, GPT-4o, Gemini Flash 1.5, DeepSeek R1 | Grok 4.20, Grok 4.1 Fast, GLM-4-Flash, MiniMax M2.5 |
| **Consensus logic** | Weighted average with confidence-adjusted weights; std dev disagreement penalty; min 3 models required | First successful provider wins; no disagreement detection |
| **Debate transcript** | Full step-by-step transcript embedded in reasoning | Single AI output |
| **Paper trading** | SQLite `signals` table + static HTML dashboard with Chart.js equity curve | SQLite `pnl.db` + JSON proof files; no HTML dashboard |
| **Signal schema** | market_id, title, side, entry_price, confidence, reasoning, strategy, outcome, settlement_price, pnl | market_id, side, price, size, timestamp, pnl |
| **Position sizing** | Quarter-Kelly (0.25x) | Kelly criterion via `position_sizer.py` |
| **Daily AI budget** | $50/day max (beast mode: $15/day) | ~$0 (already paid; marginal cost per call ~$0.003) |
| **Risk: max daily loss** | 10% (discipline) / 15% (beast) | 3% ($30 on $1000 bankroll) |
| **Risk: max drawdown** | 15% | 1% per phase |
| **Risk: max position** | 3% per position | 1–3% per position per venue |
| **Exit strategies** | Quick-flip scalping (immediate limit sell), max_hold_time (30 min), trailing take-profit (todo), confidence-decay exit (todo) | EV filter; no formal exit framework |
| **Market making** | 40% capital allocation | None ( Kalshi maker-only orders) |
| **Calibration tracking** | `logs/ensemble_calibration.json` with resolution backfill | None |
| **Confidence threshold** | 0.55–0.65 depending on category | 0.50 (flat prior) for Bayesian; 0.55 for stocks |

---

## PORTABLE PATTERNS

### 1. Debate Mode for Sentiment Scorer (HIGH Priority)

**What:** Replace single-provider cascade with a role-based multi-agent debate.

**Why:** Their architecture produces richer signal. A bull/bear debate surfaces both bull and bear cases before deciding. The disagreement penalty (std dev > 0.25 threshold) is a genuine edge — our bot currently has no mechanism to detect when providers strongly disagree.

**Estimated effort:** ~150 lines Python

**Pseudocode for our `sentiment_scorer.py`:**

```python
# New function: debate_score_market()
async def debate_score_market(market: dict, providers: list) -> dict:
    """
    Run a structured debate instead of single-provider cascade.

    Step 1 (parallel): Forecaster + News Analyst pre-analysis
    Step 2: Bull researcher (YES case) — prompt includes bull persona
    Step 3: Bear researcher (NO case) — prompt includes bear persona
    Step 4: Aggregate with disagreement penalty

    Returns same dict as score_market() plus:
      - "disagreement": float (std dev of model probabilities)
      - "debate_transcript": str
      - "bull_prob": float
      - "bear_prob": float
    """
    import numpy as np

    # Run bull + bear in parallel using first available provider
    provider = providers[0]

    bull_result = _call_provider_with_persona(provider, market, "bull_researcher")
    bear_result = _call_provider_with_persona(provider, market, "bear_researcher")
    forecaster_result = _call_provider(provider, market)  # True probability estimate

    probs = [forecaster_result["probability"], bull_result["probability"], bear_result["probability"]]
    weights = [0.40, 0.30, 0.30]

    # Weighted average
    blended = sum(p * w for p, w in zip(probs, weights))

    # Disagreement penalty
    std_dev = np.std(probs)
    disagreement_threshold = 0.20
    if std_dev > disagreement_threshold:
        penalty = min(1.0, std_dev / disagreement_threshold) * 0.25
        blended *= (1 - penalty)

    return {
        "probability": max(0.01, min(0.99, blended)),
        "confidence": 1.0 - std_dev,  # Lower confidence when models disagree
        "disagreement": std_dev,
        "bull_prob": bull_result["probability"],
        "bear_prob": bear_result["probability"],
        "debate_transcript": f"BULL: {bull_result['reasoning']}\nBEAR: {bear_result['reasoning']}",
        "provider": f"debate-{provider}",
    }
```

**Key insight:** We don't need 5 separate API calls. We can run a single LLM with a bull/bear debate prompt in one call — structured output with two `probability` fields. This is how they do it too (the Trader agent sees all prior results as context).

---

### 2. Shadow Results HTML Dashboard (MEDIUM Priority)

**What:** Replace our JSON proof files + manual log inspection with a static HTML dashboard.

**Why:** Their `src/paper/dashboard.py` generates a self-contained HTML file (Chart.js for equity curve, signal table with outcome badges, cumulative P&L). This is immediately useful for reviewing trading performance visually.

**Estimated effort:** ~100 lines Python + ~60 lines HTML/CSS

**Implementation notes:**
- Read from our `paper_trading/pnl.db` (already exists)
- Generate HTML on-demand or after each run
- Key chart: cumulative P&L over time (from trade timestamps)
- Signal table: timestamp, ticker, side, entry, outcome, PnL
- No server needed — static HTML can be opened locally or served via GitHub Pages

**Signal schema mapping:**
Our `pnl.db` trades table → their `signals` table
| Their field | Our equivalent |
|---|---|
| market_id | ticker |
| side | side |
| entry_price | price |
| confidence | computed (AI cascade confidence) |
| reasoning | verdict/notes |
| strategy | phase (kalshi/stock_hunter/etc) |
| outcome | resolved YES/NO |
| settlement_price | resolution |
| pnl | pnl |

---

### 3. Exit Strategy Framework (MEDIUM Priority)

**What:** Implement a formal exit strategy system for managing open positions.

**Why:** Our bot opens positions but has no systematic exit logic. Their quick-flip scalping (buy at 1–20¢, immediately place limit sell at target, max hold 30 min) is one pattern. A more relevant one: **confidence-decay exit** — if AI confidence drops X% from entry, exit position.

**Estimated effort:** ~200 lines Python

**For our use case:**
```
Entry: AI confidence = 0.70
Exit triggers:
  - confidence_decay: if current_confidence < entry_confidence * 0.75 → exit
  - time_based: if hold_time > 24 hours AND confidence < 0.60 → exit
  - pnl_based: if pnl > 2x expected_value OR pnl < -0.5 * expected_value → exit
```

---

## ANTI-PATTERNS TO AVOID

### 1. $50/day AI Budget — Wrong for Our Scale

Their `daily_ai_cost_limit = 50.0`. At their scale (likely $10K+ bankroll), $50/day AI spend = 0.5%/day in costs. At our $100 bankroll, $50/day = 50%/day in AI costs — instantly negative EV.

**Our situation:** We already have Grok API pre-paid. Marginal cost per call ~$0.003–$0.01. Our effective AI "budget" is essentially unlimited at our trade frequency.

**Action:** Do NOT adopt their cost-limiting logic. Our cascade should call as many providers as needed per market without budget anxiety.

### 2. 15% Max Drawdown — Too Loose for Our Risk Appetite

Their `max_drawdown = 0.15`. At $100 bankroll that's a $15 loss before the circuit breaker trips. Our current `max_daily_loss_usd = 30.0` on a $100 bankroll is already 30%/day — we should tighten this, not loosen it.

**Our discipline target:** 3–5% max daily loss ($3–$5 on $100). Their 15% drawdown limit is a "beast mode" legacy that they themselves say caused catastrophic losses.

### 3. 40% Market Making Allocation — Wrong Strategy at Our Scale

Their `UnifiedAdvancedTradingSystem` allocates 40% to market making. This requires:
- Large capital base to absorb inventory risk
- Wide bid-ask spreads to profit from
- Sophisticated inventory management

**Our bankroll ($100) and market (prediction markets with 1¢ spreads on cheap contracts):** Market making generates pennies per trade while taking on two-sided risk. Not viable.

**Action:** Skip entirely. Our maker-only Kalshi approach (placing limit orders slightly below mid-price) already captures some spread benefit without inventory risk.

### 4. 5-Model Parallel Ensemble — Overkill / Expensive

Running Grok-3 + Claude + GPT-4o + Gemini + DeepSeek in parallel per market decision = ~$2–5 per market in API costs (at OpenRouter pricing). At 20 markets per scan × 2 scans/day = $80–200/day in AI costs.

**Our approach (cascade):** One successful call per market = ~$0.01/market. 40× cheaper.

**Verdict:** Their 5-model approach is viable at their capital scale (where $200/day AI spend on $50K bankroll = 0.4%/day). At our scale it's 40× more expensive than our cascade and produces similar quality outputs.

---

## RECOMMENDED INTEGRATION ORDER

### Phase 1: Debate Mode (Week 1)
**Dependencies:** None — only modifies `sentiment_scorer.py`
- Add `debate_score_market()` function
- Single provider call with structured bull/bear debate prompt
- Disagreement penalty on top of probability
- Backward-compatible: existing cascade continues if debate fails

### Phase 2: HTML Dashboard (Week 2)
**Dependencies:** None
- Read from existing `paper_trading/pnl.db`
- Generate HTML on `python3 scripts/overnight_report.py` completion
- Add to existing report pipeline

### Phase 3: Exit Strategy Framework (Week 3–4)
**Dependencies:** Phase 1 (debate mode confidence scores)
- Track entry confidence per position
- Confidence-decay monitor in overnight report
- No changes to trading loop required

---

## KEY CODE REFERENCES

| Pattern | Their file | Our equivalent |
|---|---|---|
| Ensemble aggregator | `src/agents/ensemble.py` | `strategies/sentiment_scorer.py` |
| Debate runner | `src/agents/debate.py` | — (new) |
| Forecaster persona | `src/agents/forecaster_agent.py` | — (new prompt) |
| Bull researcher | `src/agents/bull_researcher.py` | — (new prompt) |
| Paper tracker | `src/paper/tracker.py` | `paper_trading/pnl.db` |
| HTML dashboard | `src/paper/dashboard.py` | — (new) |
| Risk settings | `src/config/settings.py` | `config.py` |
| Portfolio enforcer | `src/strategies/portfolio_enforcer.py` | Risk caps in `config.py` |
| Quick-flip exit | `src/strategies/quick_flip_scalping.py` | — (reference only) |

---

## SPECIFIC CONFIG VALUES TO NOTE

Their "Discipline Mode" settings (which they recommend over beast mode):

| Setting | Discipline | Beast | Our Current |
|---|---|---|---|
| `kelly_fraction` | 0.25 | 0.75 | 0.25–0.50 (fractional) |
| `max_position_size_pct` | 3% | 5% | 1–3% per venue |
| `max_daily_loss_pct` | 10% | 15% | 3% ($30 on $100) |
| `min_confidence_to_trade` | 0.60 | 0.55 | 0.50 for Bayesian |
| `disagreement_threshold` | 0.25 | — | N/A (no disagreement detection) |

Their "NCAAB NO-side" finding (74% WR, +10% ROI) is notable — sports markets appear more predictable than economic ones. Our Kalshi bot currently blocks sports via `KALSHI_BLOCKED_CATEGORIES`. Worth revisiting.

---

## SUMMARY

Their bot is architecturally sophisticated at the cost of complexity and AI spend. The primary stealable ideas are:
1. **Debate mode** — most impactful, low cost to implement
2. **HTML dashboard** — useful for human review, easy to build
3. **Exit framework** — nice-to-have, requires debate mode first

The anti-patterns (loose risk params, expensive 5-model ensemble, market making) are all wrong for our $100 bankroll and should be explicitly rejected.
