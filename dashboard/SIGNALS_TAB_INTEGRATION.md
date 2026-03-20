# Signals Tab Integration Guide

## Overview

The `signals-tab.jsx` component provides a real-time view of every signal source in the Bayesian trading cascade. It is fully standalone — works with mock data out of the box, and switches to live API data once the API is reachable.

## Files Added

- `dashboard/signals-tab.jsx` — standalone React component
- `strategies/signal_aggregator.py` — backend signal collector
- `api_server.py` — new endpoints added:
  - `GET /api/signals/current` — live signal state (60s cache)
  - `GET /api/signals/history?hours=24&interval=30` — time-series for sparklines

## How to Add the Tab to crypto-dashboard-v6.jsx

### 1. Add the tab entry

Find the tab definitions (usually an array called `tabs` or `tabConfig`) and add:

```jsx
{ id: "signals", label: "Signals", icon: "📡" }
```

### 2. Import the component

```jsx
import SignalsTab from "./signals-tab";
```

(Adjust the import path relative to where the tab config lives.)

### 3. Add the route/case

In the tab's render/case block:

```jsx
case "signals":
  return <SignalsTab />;
```

### 4. (Optional) Wire up live data fetching

The component fetches its own data via `useEffect` with a 60s poll interval. To pre-fetch data from the parent dashboard instead:

```jsx
// In the parent component (crypto-dashboard-v6.jsx):
const [signalsData, setSignalsData] = useState(null);
const [signalsHistory, setSignalsHistory] = useState(null);

useEffect(() => {
  const API = "http://192.168.68.64:8510";
  Promise.all([
    fetch(`${API}/api/signals/current`).then(r => r.json()),
    fetch(`${API}/api/signals/history?hours=24`).then(r => r.json()),
  ]).then(([current, history]) => {
    setSignalsData(current);
    setSignalsHistory(history.history || []);
  }).catch(console.error);
}, []);

// Pass to tab:
case "signals":
  return <SignalsTab initialData={signalsData} initialHistory={signalsHistory} />;
```

## API Endpoints

### GET /api/signals/current

Returns the current state of all 5 signal sources:

```json
{
  "timestamp": "2026-03-20T12:00:00Z",
  "collection_time_ms": 99.1,
  "sources": {
    "gdelt": { "status": "ok", "geo_risk_score": 0.42, "event_count": 23, ... },
    "grok":  { "status": "ok", "model": "grok_420", "last_sentiment": 0.71, ... },
    "glm":   { "status": "stale", "model": "glm", "last_sentiment": 0.68, ... },
    "stock_hunter": { "status": "ok", "active_tickers": 5, "top_signals": [...], ... },
    "kelly": { "status": "ok", "current_fraction": 0.25, "edge_threshold": 0.015, ... }
  },
  "cascade_summary": { "total_sources": 5, "sources_healthy": 4, "sources_degraded": 1, "sources_down": 0, ... }
}
```

### GET /api/signals/history?hours=24&interval=30

Returns an array of snapshots sampled at `interval` minutes:

```json
{
  "history": [
    { "timestamp": "2026-03-20T11:30:00Z", "gdelt_risk": 0.40, "stock_sentiment": 0.62, "top_ticker": "NVDA", "top_signals": [...] },
    ...
  ],
  "hours": 24,
  "interval_minutes": 30
}
```

## Status Indicators

| Status  | Color     | Meaning                          |
|---------|-----------|----------------------------------|
| ok     | `#00ff9d` | Fresh data, working correctly    |
| stale  | `#ffbe0b` | Data is >1h old but recoverable  |
| error  | `#ff4757` | Source failed (check logs)       |
| disabled | `#666`   | Provider not configured          |

## Source Details

| Source       | Data                                      | Staleness threshold |
|--------------|-------------------------------------------|-------------------|
| GDELT        | `geo_risk_score`, `event_count`, `tone`  | 1 hour            |
| Grok         | `last_sentiment`, `model`, `last_market`  | 2 hours           |
| GLM          | `last_sentiment`, `last_market`           | 2 hours           |
| Stock Hunter | Top 10 tickers, sentiment, news source    | 2 hours           |
| Kelly        | Fraction, edge threshold, positions sized | File mtime        |

## Mock Data

The component ships with realistic mock data (`MOCK_SIGNALS` and `MOCK_HISTORY`) so it is fully testable before the API is deployed. The mock shows:
- GDELT: ok with 23 events
- Grok: ok
- GLM: stale (3h old)
- Stock: 5 tickers with varying sentiment
- Kelly: 3 positions sized, 5.8% drawdown

To force mock mode in development, simply don't pass `initialData`/`initialHistory` props and ensure the API is unreachable.

## API Base URL

The component uses `const API_BASE = 'http://192.168.68.64:8510'`. Update this to match your NUC's local IP before deployment.

## Port Note

The API server runs on **port 8510** (not 8501 which is Streamlit's default). Make sure the NUC's firewall allows inbound connections on 8510 if accessing from a different machine.
