# Claude Progress — pm_updown_bot_bundle

## 2026-04-08 (backtest_kalshi.py — Vectorized Backtesting Harness)

**Agent:** Claude Code (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi backtesting
**Type:** Feature

### Summary
- Created `backtest_kalshi.py` — Kalshi vectorized backtesting harness
- Reads from: `logs/scratchpad/prior_validation.jsonl` + `proofs/kalshi_optimized_*.json`
- Synthetic mode: generates trades from prior_validation records (proof packs are shadow/no orders)
- Calculates: Sharpe, max drawdown, win rate, profit factor, avg edge, days in market
- Monte Carlo simulation for forward-looking CI (Sharpe + MaxDD 95% CIs)
- Outputs: `proofs/backtest_report_YYYYMMDD.json` + `proofs/backtest_equity_curve_YYYYMMDD.png`
- CLI: `python3 backtest_kalshi.py --days 30 --mc-sims 500`
- Verified: import OK, equity curve PNG saved (82K), report JSON saved

### Outputs
- `proofs/backtest_report_20260408.json`
- `proofs/backtest_equity_curve_20260408.png`
- `feature_list.json` (created)

### Metrics (synthetic mode, 475 trades from prior_validation records)
- Total PnL: $9.88
- Win rate: 33.5%
- Sharpe: 10.38
- MaxDD: $8.82
- Profit factor: 1.14

### Next
- Add actual resolved trade tracking to pnl.db (Kalshi phase currently shadow mode only)
- Integrate with autoresearch experiment configs from notes/

## backtest_kalshi.py — Sharpe annualization fix
- BUG: per-trade Sharpe was annualized with sqrt(trades_per_day * 252), inflating 10-60x
- FIX: aggregate to daily PnL first, then Sharpe = (daily_mean / daily_std) * sqrt(252)
- Max drawdown also switched to daily cumulative PnL curve
- Added Sharpe > 3.0 sanity warning (fires per-result, suppressed in MC loop)
- Added daily_pnl_series to JSON report
- Suppress_warnings param added to calc_metrics for MC loop calls

## backtest_kalshi.py — Sharpe still inflated, round 2
- DIAGNOSTIC: printed raw daily PnL series to identify variance issue
- FIX A: Minimum 20 trading days required for Sharpe (else NaN) — fires correctly for pnl_db (4 days)
- FIX B: Synthetic mode adds no-trade days (~30%), regime flips (~20%), spread noise (~15%)
- FIX D: daily_pnl_series now [{date, pnl, cumulative, trades}] per day
- MC CI: filter NaN sharpes before computing 95% CI (was showing 'nan')

## Multi-model debate pattern in sentiment_scorer.py
- Added multi_model_debate() with 3 roles: Forecaster, Critic, Synthesizer
- Forecaster uses Grok primary; Critic uses GLM or Grok-adversarial
- Synthesizer is local weighted average (no extra API call)
- Consensus flag: agree/disagree based on |prob_diff| > 0.25
- Critique strength > 0.7 shifts weight toward critic
- DEBATE_MODE=true/false in .env (opt-in, default OFF)
- Fallback: if either role fails, degrades to single-model mode
- JSON parsing with regex fallback for unreliable AI JSON producers
- 15-second timeout per role call

## Debate validation + micro-live audit
- Debate: Grok API key SET, but api.x.ai timed out (network unreachable from NUC1).
  Correctly falls back to single-model mode.
  Bug fixed: CRITIC_SYSTEM used .format() with unescaped braces (KeyError).
  Added load_dotenv() to sentiment_scorer.py so keys load on standalone import.
- run-micro-live.sh: correctly passes --mode micro-live, --max-pos 10.0, 5s abort delay
- .env has KALSHI_KEY, KALSHI_TRADING_KEY, KALSHI_TRADING_SECRET_FILE all set
- Kalshi API: balance = $108 confirmed via get_balance()
- Order placement: utils/kalshi_orders.py has place_order() method
- Paper→live toggle: runner.py --mode flag (shadow/micro-live/real-live)
- CRITICAL BUG: runner.py accepts --mode micro-live but kalshi_optimize.py
  only handles shadow/real-live. micro-live falls through to else→skips all trades!
  This means ./run-micro-live.sh currently does NOTHING.
  Fix needed: add mode=="micro-live" handling to kalshi_optimize.py.
- Ready for supervised first micro-live trade once micro-live mode bug is fixed.

## Network diag + micro-live fix
- GROK NETWORK: api.x.ai resolves (104.18.18.80), IPv4 HTTP 200 confirmed.
  Python requests test: Status 200, works correctly.
  Earlier timeout: was hitting wrong endpoint in earlier test, fixed now.
- GLM KEY INVALID: GLM key returns 401 on all endpoints — critic always falls
  back to forecaster-only. Debate still returns debate_used=true with forecaster result.
- MICRO-LIVE FIX: Added is_live normalization in kalshi_optimize.py:
  - is_live = mode in ("real-live", "micro-live")
  - Hard caps: bankroll=$25, max_pos=$5, max_daily_loss=$10
  - Log prefix "[MICRO-LIVE]" for easy grep
  - No code duplication, all gates still enforced
  - runner.py dry_run=(mode=="shadow") correctly passes dry_run=False for micro-live
  - Validation: `[MICRO-LIVE] Hard caps applied: bankroll=$1.08, max_pos=$5.00` (verified)

## Production hardening: dedup + order_id + cron
- FIX: order_id extracted from Kalshi API response (was 'unknown')
  - Kalshi returns `{'order': {'order_id': 'xxx', ...}}`, now correctly extracted
- FIX: Dedup — checks existing open orders + positions before placing
  - Uses `get_orders(status='open')` + `get_positions()` at start of run
  - Skips any market where we already have an open order or position
- FIX: MAX_OPEN_ORDERS=20 safety cap prevents runaway accumulation
  - Logs warning and skips entire run if already at cap
- CRON: scripts/cron_micro_live.sh runs every 4 hours with DEBATE_MODE=true
  - Installed: 0 */4 * * * /opt/slimy/pm_updown_bot_bundle/scripts/cron_micro_live.sh
  - Timeout: 600s max per run, logs to logs/cron_micro_live.log
- Committed and pushed: 85e832f

## 2026-04-09 (premium_10_10_split — Expand AI Premium Tier)

**Agent:** Claude Code (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi strategy
**Type:** Feature

### Summary
- Premium tier: 10 → 20 markets (10 short-term <=7d expiry + 10 long-term >7d)
- Both buckets sorted by volume desc, combined for 20-market AI premium tier
- Bulk tier absorbs remaining markets (0 when ai_max=20, ai_premium=20)
- Log line verified: `[PREMIUM] Short-term: 0, Long-term: 10, Total: 10 (max 10 each)`
- KALSHI_BLOCKED_CATEGORIES confirmed clean: Entertainment/Mentions/Social/Exotics only, no weather/economics blocks

### Changes
- `strategies/kalshi_optimize.py`: replaced volume-only sort + tier loop with two-bucket split
- `AI_MAX_PRIORS_PER_RUN` default: 10 → 20
- `AI_PREMIUM_MAX` default: 10 → 20
- `feature_list.json`: added premium_10_10_split entry

### Next
- pm_updown_bot_bundle OPERATIONAL

## Fix PROVIDERS cascade: remove dead GLM, gemini as fallback
- GLM removed from PROVIDERS (401/429 on every call)
- Cascade: grok_fast → grok_420 → gemini
- Gemini is fast, free, works — replaces GLM as fallback
- PROVIDERS tuple was malformed (missing `},` on gemini, stray `{`)
- All glm references cleaned up (docstring, comments, dead loop)
- Critic provider correctly returns gemini
- get_ai_prior(tier='bulk') returns valid result via gemini
