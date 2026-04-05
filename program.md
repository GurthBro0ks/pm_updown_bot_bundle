# Autoresearch Program — pm_updown_bot_bundle

## GOAL
Maximize `composite_score` from `scripts/autoresearch_run.sh`. Higher is better.

## BASELINE
- **-2.9516** (composite, as of 2026-03-20)
- Sharpe: -1.9273, Max Drawdown: 0.58%, Turnover: 0.029/hr
- 14 trades, 33% win rate, 2 active features
- Stock phase has 19 days of proofs. Kalshi has live auth (206 markets) but thin proof data.

## SCOPE
Files that may be modified:
- `strategies/kalshi_optimize.py` — Kalshi market selection, edge threshold, position sizing
- `strategies/stock_hunter.py` — Stock sentiment scoring, RSI period, thresholds
- `strategies/sentiment_scorer.py` — AI cascade (Grok/GLM/Finnhub), GDELT weight
- `strategies/gdelt_signal.py` — GDELT geo_risk_score thresholds (0.3/0.7 bands)
- `config.py` — Parameters only (RSI period, Kelly fraction, ATR stop, etc.)

## METRIC
`composite_score` — printed as `SCORE: X.XXXX` by `bash scripts/autoresearch_run.sh`

Formula:
```
composite = sharpe_ratio - (max_drawdown_pct * 2.0) - (turnover_rate * 0.5) + (simplicity_bonus * 0.3)
simplicity_bonus = 1.0 / feature_count
```

## VERIFY
```bash
cd /opt/slimy/pm_updown_bot_bundle
bash scripts/autoresearch_run.sh
```

## RULES
1. **ONE change per experiment.** Never modify two files or two parameters at once.
2. **Read git log + experiments.tsv before each iteration.** Do not repeat experiments.
3. **SIMPLICITY CRITERION:** Removing a feature and achieving equal/better score is a **WIN**. Keep the removal. Tiny gain + added complexity = **DISCARD**.
4. **REMOVAL BIAS:** For the first 25 experiments, prioritize **REMOVING** features one at a time. Log every removal's impact.
5. **PARAMETER TUNING:** After feature pruning (≥25 removal experiments logged), sweep:
   - RSI lookback period (try 6, 8, 10, 12 vs current 14)
   - Sentiment thresholds in stock_hunter.py
   - Edge threshold in kalshi_optimize.py
   - Kelly fraction bounds
   - ATR trailing stop multiplier
   - GDELT geo_risk_score thresholds (0.3/0.7 — try 0.2/0.6, 0.4/0.8)
6. **DO NOT TOUCH:** Risk caps (`max_pos_usd`, `max_daily_loss_usd`), proof generation, API credentials, logging infrastructure.
7. **Every 25 experiments:** Run `python3 scripts/autoresearch_scorer.py --proof-dir data/holdout/` — log holdout score but **DO NOT optimize against it**.
8. **Max 50 experiments per overnight run.** Stop at 50 regardless of progress.
9. **Log every experiment** via `python3 scripts/experiment_log.py --log "hypothesis" --file <changed_file> --type <change_type> --before <score> --after <score> --kept <y|n>`
10. **Git commit** kept changes: `git add -A && git commit -m "experiment: {hypothesis}"`
11. **Git revert** discarded changes immediately: `git checkout -- <file>`

## HYPOTHESES TO TEST

### STOCK-PHASE EXPERIMENTS (experiments 1–30)
Stock phase has 19 days of proof data and 14 trades. Run these first.

1. Remove Stocktwits sentiment (already returning None/disabled)
2. Remove keyword-based news scoring in stock_hunter.py
3. Change RSI period from 14 to 8 (biggest win in reference experiment)
4. Remove Marketaux if Alpha Vantage covers same signal
5. Adjust Kelly fraction bounds
6. Tune GDELT weight in Bayesian cascade (0.10, 0.15, 0.20, 0.25)
7. Test market_price baseline (0.5 vs 0.45 vs 0.55) — neutral baseline that edge is calculated against; was a major bug source (was 1.0!)
8. Test Finnhub/AlphaVantage blend ratio with Grok (currently 50/50, try 30/70, 40/60, 60/40 in favor of Grok)
9. Test position size floor (currently min_position_usd=1.00, try 2.00, 3.00)
10. Test whether combined_sentiment threshold matters

### KALSHI EXPERIMENTS (experiments 31–50)
> **Note:** Kalshi auth fixed 2026-03-20 (commit 7a6f20a, 206 markets returned). Proof data is thin — only run these after stock-phase experiments to let Kalshi proofs accumulate during market hours.

11. Simplify kalshi_optimize edge calculation
12. Tune Kalshi category filters
13. Adjust Kalshi maker edge formula parameters

## LOGGING
```bash
# After each experiment:
python3 scripts/experiment_log.py --log "removed Stocktwits API call" --file strategies/stock_hunter.py --type feature_remove --before -2.9589 --after -2.8500 --kept y

# Morning review:
python3 scripts/experiment_log.py --summary
python3 scripts/autoresearch_scorer.py --proof-dir data/holdout/ --verbose
git log --oneline -20
```

## OVERNIGHT RUN
```bash
screen -S autoresearch
cd /opt/slimy/pm_updown_bot_bundle
# Launch Claude Code and run: /autoresearch
```
