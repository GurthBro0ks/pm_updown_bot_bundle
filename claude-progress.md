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
