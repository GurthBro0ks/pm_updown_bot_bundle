# Local Deployment Steps - pm_updown_bot_bundle

## Prerequisites

1. **Python 3.8+** installed
2. **git** installed
3. **Kalshi API credentials** (set in `.env`)
4. **Polymarket API credentials** (set in `.env`)

## Setup

### 1. Clone the Repository
```bash
cd /opt/slimy
git clone https://github.com/slimywatch/pm_updown_bot_bundle.git
cd pm_updown_bot_bundle
```

### 2. Create Environment File
Create a `.env` file with your credentials:
```bash
cp .env.example .env
# Edit .env with your API keys
```

Required variables:
- `KALSHI_KEY` - Kalshi API key
- `KALSHI_SECRET` - Kalshi API secret
- `POLYMARKET_API_KEY` - Polymarket API key (optional)

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

Or with pipenv:
```bash
pipenv install
pipenv shell
```

## Running the Bot

### Shadow Mode (Recommended for Testing)
```bash
cd /opt/slimy/pm_updown_bot_bundle
python runner.py --venue=kalshi --mode=shadow
```

### Paper Trading
```bash
python runner.py --venue=polymarket --mode=paper
```

### Micro-Live Mode (Limited Risk)
```bash
python runner.py --venue=kalshi --mode=micro-live
```

### Production Mode (Requires Approval)
```bash
python runner.py --venue=kalshi --mode=live
```

## Available Venues

| Venue | Command |
|-------|---------|
| Kalshi | `--venue=kalshi` |
| Polymarket | `--venue=polymarket` |
| SEF | `--venue=sef` |

## Available Modes

| Mode | Risk | Description |
|------|------|-------------|
| `shadow` | None | Simulates trades, logs decisions |
| `paper` | None | Simulated execution with real-time odds |
| `micro-live` | Low | Real trades with strict limits ($10 max position, $50 daily loss) |
| `live` | High | Full trading - requires explicit approval |

## Risk Limits

Default risk caps (in `runner.py`):
- `max_pos_usd`: $10
- `max_daily_loss_usd`: $50
- `max_open_pos`: 5
- `max_daily_positions`: 20

## Monitoring

### Check Logs
```bash
tail -f /opt/slimy/pm_updown_bot_bundle/logs/runner.log
```

### Check Proofs
```bash
ls -la /opt/slimy/pm_updown_bot_bundle/proofs/
```

### Check Process
```bash
ps aux | grep runner.py
```

## Deployment Checklist

- [ ] Environment variables configured
- [ ] Dependencies installed
- [ ] Shadow mode tested successfully
- [ ] Risk limits reviewed
- [ ] Logs directory exists and is writable
- [ ] Proofs directory exists
- [ ] Discord/Telegram alerts configured (optional)

## Troubleshooting

### API Connection Issues
- Verify API keys in `.env`
- Check network connectivity
- Review API rate limits in `docs/API_LIMITS.md`

### Permission Errors
- Ensure log and proof directories are writable
- Check file permissions on `.env` (should be 600)

### Bot Not Trading
- Verify `--venue` and `--mode` flags
- Check that market conditions meet bot criteria
- Review logs for decision reasoning
