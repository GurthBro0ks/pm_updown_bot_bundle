# Kalshi Orderbook Signal Setup

## What This Module Does

The orderbook signal collector (`scripts/collect_orderbook.py`) is a lightweight,
read-only data collection layer that captures Kalshi market microstructure:

1. **Orderbook depth** — top 10 bid levels for both yes/no sides
2. **Trade tape** — last 100 public trades
3. **Event detection** — automatically flags:
   - `large_wall`: single price level ≥ $100 (configurable)
   - `large_fill`: single trade ≥ $50 (configurable)
   - `volume_spike`: recent trade volume ≥ 3× 24h volume
   - `spread_compression`: bid-ask spread ≤ 1 cent
   - `depth_imbalance`: one side has ≥ 3× the depth of the other

Data is stored in a separate SQLite database (`paper_trading/orderbook_snapshots.db`)
to avoid touching the production `pnl.db`.

## How to Run Manually

```bash
cd /opt/slimy/pm_updown_bot_bundle

# Dry-run (fetch and detect, but do not store)
python3 scripts/collect_orderbook.py --max-markets 20 --dry-run

# Live collection (stores to DB)
python3 scripts/collect_orderbook.py --max-markets 20

# Custom thresholds
ORDERBOOK_WALL_MIN_USD=200 ORDERBOOK_LARGE_FILL_USD=100 \
  python3 scripts/collect_orderbook.py --max-markets 10
```

## How to Add to Cron

Suggested crontab entry, offset by 5 minutes from the trading cron:

```bash
# Run orderbook collection every 4 hours, 5 minutes after the trading cron
5 */4 * * * cd /opt/slimy/pm_updown_bot_bundle && /usr/bin/python3 scripts/collect_orderbook.py --max-markets 20 >> /opt/slimy/pm_updown_bot_bundle/logs/orderbook.log 2>&1
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ORDERBOOK_WALL_MIN_USD` | 100 | Minimum size to flag a large wall |
| `ORDERBOOK_LARGE_FILL_USD` | 50 | Minimum trade count to flag a large fill |
| `ORDERBOOK_VOLUME_SPIKE_MULT` | 3.0 | Multiplier vs 24h volume for spike detection |
| `ORDERBOOK_DEPTH_IMBALANCE_RATIO` | 3.0 | Ratio threshold for depth imbalance |
| `ORDERBOOK_SPREAD_COMPRESSION_CENTS` | 0.01 | Maximum spread to flag compression |

## Database Location

```
paper_trading/orderbook_snapshots.db
```

Schema:
```sql
CREATE TABLE orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT 'kalshi',
    ticker TEXT NOT NULL,
    question TEXT,
    yes_bid REAL,
    yes_ask REAL,
    spread_cents REAL,
    volume_24h REAL,
    open_interest REAL,
    orderbook_json TEXT,
    recent_trades_json TEXT,
    events_json TEXT,
    event_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
```

## How to Query Collected Data

### All snapshots for a ticker
```sql
SELECT timestamp_utc, yes_bid, yes_ask, event_count
FROM orderbook_snapshots
WHERE ticker = 'KXETHY-27JAN0100-B1625'
ORDER BY timestamp_utc DESC
LIMIT 100;
```

### Only snapshots with events
```sql
SELECT timestamp_utc, ticker, event_count, events_json
FROM orderbook_snapshots
WHERE event_count > 0
ORDER BY timestamp_utc DESC
LIMIT 100;
```

### Large wall events over time
```sql
SELECT timestamp_utc, ticker, events_json
FROM orderbook_snapshots
WHERE events_json LIKE '%large_wall%'
ORDER BY timestamp_utc DESC;
```

### Average spread by ticker
```sql
SELECT ticker, AVG(spread_cents), COUNT(*)
FROM orderbook_snapshots
GROUP BY ticker
HAVING COUNT(*) >= 10
ORDER BY AVG(spread_cents) ASC;
```

### Python example
```python
from signals.orderbook_store import get_event_snapshots

events = get_event_snapshots(since="2026-05-01T00:00:00Z", limit=50)
for e in events:
    print(e["ticker"], e["timestamp_utc"], e["event_count"])
```

## Analysis After 2–4 Weeks

After collecting 2–4 weeks of data, you can run:

```sql
-- Which event types correlate with price moves?
SELECT
    json_extract(value, '$.type') as event_type,
    COUNT(*) as count,
    AVG(yes_bid) as avg_yes_bid,
    AVG(yes_ask) as avg_yes_ask
FROM orderbook_snapshots,
     json_each(events_json) as events
WHERE timestamp_utc >= datetime('now', '-14 days')
GROUP BY event_type;
```

```sql
-- Do large walls predict price direction?
SELECT
    ticker,
    json_extract(value, '$.side') as wall_side,
    json_extract(value, '$.details.price') as wall_price,
    yes_bid,
    LAG(yes_bid) OVER (PARTITION BY ticker ORDER BY timestamp_utc) as prev_yes_bid
FROM orderbook_snapshots,
     json_each(events_json) as events
WHERE json_extract(value, '$.type') = 'large_wall'
  AND timestamp_utc >= datetime('now', '-14 days');
```

## Safety

- This module is **read-only** against the Kalshi API
- It does **not** place trades or modify order state
- It runs in a separate SQLite database from `pnl.db`
- It does not modify `runner.py` or any trading strategy
- Rate limiting: 0.5s sleep between per-market API calls
