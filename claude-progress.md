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
