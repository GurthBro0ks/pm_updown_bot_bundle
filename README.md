# PM Updown Bot Bundle

Multi-venue trading bot with 4 phases for systematic market participation.

## Overview

This bot runs 4 phases in sequence:
- **Phase 1**: Kalshi Optimization (election markets)
- **Phase 2**: SEF Spot Trading (DeFi arbitrage)
- **Phase 3**: Stock Hunter (sentiment-based stock trading)
- **Phase 4**: Airdrop Farming (on-chain interaction tracking)

## Legal Status

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1 | ✅ Legal | CFTC-regulated election markets |
| Phase 2 | ✅ Legal | Spot DEX/CEX only, no derivatives |
| Phase 3 | ✅ Legal | SEC-regulated stocks via Finnhub |
| Phase 4 | ✅ Legal | Michigan-legal spot DeFi interactions |

**DISCLAIMER**: This is not financial advice. All trading involves risk. This bot is for educational purposes only.

## Configuration

All configuration is centralized in `config.py`. No hardcoded API keys in strategy files.

### API Keys (set in .env)

```
FINNHUB_API_KEY=...
ALPHA_VANTAGE_API_KEY=...
MASSIVE_API_KEY=...
KALSHI_KEY=...
KALSHI_SECRET_FILE=./kalshi_private_key.pem
```

### Phase Enable/Disable

Edit `config.py` to enable/disable phases:

```python
PHASES = {
    "phase1_kalshi": True,
    "phase2_sef": True,
    "phase3_stock_hunter": True,
    "phase4_airdrop": True,
}
```

### Risk Parameters

All risk parameters are in `config.py` under `RISK_CAPS`:

```python
RISK_CAPS = {
    # Global limits
    "max_pos_usd": 10.0,
    "max_daily_loss_usd": 30.0,
    "max_open_pos": 3,

    # Stock Hunter specific
    "stock_sentiment_threshold": 0.55,
    "stock_min_price_usd": 1.0,
    "stock_max_price_usd": 5.0,
}
```

### Meme Ticker Discount

Meme stocks (GME, AMC, etc.) get a 0.7x sentiment discount to reduce false positives:

```python
MEME_TICKERS = {'GME', 'AMC', 'BBBY', 'PLTR', 'MARA', 'RIOT', 'DWAC', 'SPCE', 'HOOD', 'DJT'}
```

## Running the Bot

### Shadow Mode (Paper Trading)

```bash
python3 runner.py --mode shadow --phase all
```

### Micro-Live Mode (Real Trades with Risk Gates)

```bash
python3 runner.py --mode micro-live --phase all
```

### Run Specific Phase

```bash
python3 runner.py --mode shadow --phase phase3
```

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--mode` | Execution mode | shadow |
| `--phase` | Phase to run | all |
| `--bankroll` | Starting bankroll | 100.0 |
| `--max-pos` | Max position size | 10.0 |
| `--verbose` | Enable debug logging | false |

## Execution Modes

### Shadow Mode
- Paper trading with $100 virtual balance
- No real money at risk
- All signals logged
- Use for learning and strategy development

### Micro-Live Mode
- Real trades with strict risk gates
- Max position: $10
- Max daily loss: $30
- Kill switch triggers automatically

### Real-Live Mode
- Full capital deployment
- Requires manual approval
- Not recommended for beginners

## Shadow → Micro-Live Transition

Before running in micro-live:

1. ✅ Run shadow mode for 30+ days
2. ✅ Verify P&L is positive
3. ✅ Check trade log for signal quality
4. ✅ Set appropriate risk caps
5. ✅ Start with small position sizes

```bash
# Verify shadow performance
python3 runner.py --mode shadow --phase all
cat paper_trading/paper_balance.json
```

## Trade Logging

All trades are logged to `paper_trading/trade_log.json`:

```json
{
  "entries": [...],
  "checks": [...],
  "exits": [...]
}
```

## File Structure

```
pm_updown_bot_bundle/
├── config.py              # Central configuration
├── runner.py              # Main execution loop
├── strategies/
│   ├── kalshi_optimize.py
│   ├── sef_spot_trading.py
│   ├── stock_hunter.py
│   └── airdrop_farmer.py
├── utils/
│   ├── price_fetcher.py   # Unified price service
│   ├── sentiment.py        # Sentiment analysis
│   ├── rotation_manager.py # Exit logic
│   ├── kalshi.py
│   └── logging_config.py
├── paper_trading/
│   ├── paper_balance.json
│   ├── paper_pnl.db
│   ├── trade_log.json
│   └── pnl_tracker.py
└── config/
    ├── rotation_config.json
    └── micro-live.env
```

## Archived Files

Obsolete files moved to `.removed_20260227/`:
- cross_venue_arb.py
- hyperliquid.py
- polymarket.py
- predictit.py

## Troubleshooting

### Import Errors

```bash
# Test imports
python3 -c "import config; print('config OK')"
python3 -c "from utils import price_fetcher; print('OK')"
python3 -c "from utils import sentiment; print('OK')"
```

### API Rate Limits

- **Finnhub**: 60 calls/minute (free tier)
- **Alpha Vantage**: 25 calls/day (free tier)
- **Massive**: Rate limited, uses 15s delay

### Check Logs

```bash
tail -f logs/runner.log
tail -f logs/stock_hunter.log
```

## Support

- Review `docs/` for detailed documentation
- Check `notes/` for experimental features
- See `AGENTS.md` for agent configurations
