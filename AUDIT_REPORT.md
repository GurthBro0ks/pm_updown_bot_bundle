# Trading Bot Infrastructure Audit Report
**Generated:** 2026-02-27

---

## 1. Complete File Tree: pm_updown_bot_bundle

### Root Level
```
/opt/slimy/pm_updown_bot_bundle/
├── .env                           # Main environment config (API keys)
├── .git/
├── .gitignore
├── AGENTS.md
├── kalshi_private_key.pem         # RSA private key for Kalshi
├── run-arb.py                     # Main arbitrage runner
├── runner.py                      # General runner
├── run_5_real_loops.sh
├── run-micro-live.sh
├── run_shadow_2h.sh
├── run_with_monitoring.sh
```

### Config
```
config/
├── micro-live.env
└── rotation_config.json
```

### Strategies
```
strategies/
├── __init__.py
├── cross_venue_arb.py
├── kalshi_optimize.py
├── kalshi_optimize_fixed.py
├── kalshi_optimize_fixed2.py
├── kalshi_optimize_fixed3.py
├── sef_spot_trading.py           # Contains REMOVED references to dYdX/GMX
├── sef_extended_trading.py.REMOVED  # Marked as REMOVED
└── stock_hunter.py               # Contains REMOVED reference to marketaux
```

### Venues (EXCHANGE INTEGRATIONS)
```
venues/
├── hyperliquid.py                # ILLEGAL - Hyperliquid deprecated
├── polymarket.py
└── predictit.py                 # OBSOLETE - PredictIt venue
```

### Utils
```
utils/
├── __pycache__/
├── kalshi.py
├── logging_config.py
├── proof.py
└── rotation_manager.py
```

### Scripts
```
scripts/
├── hourly_shadow.py
├── live_wrapper.sh
├── ned_optimizer.py
├── overnight_report.py
├── pnl-weekly-report.sh
├── ralph/
│   ├── beads_snapshot.py
│   ├── gate.sh
│   ├── guard_forbidden.py
│   ├── loop.sh
│   └── run_loop_tmux.sh
├── run_hourly_shadow.sh
├── run_tests.sh
└── shadow_test_runner.sh
```

### Paper Trading
```
paper_trading/
├── check_pnl.sh
├── paper_balance.json
└── pnl_tracker.py
```

### Logs
```
logs/
├── av_counter.json
└── sync_20260207.json
```

### .Ralph
```
.ralph/
├── ralph.env
└── ralph_rules.json
```

### Proofs (EXTENSIVE BACKUP EVIDENCE)
```
proofs/
├── backup_20260208_100000/       # Old backup
├── polymarket-integration-20260208-165511/
│   └── backups/
└── [hundreds of kalshi_optimized_*.json files]
```

---

## 2. Complete File Tree: hybrid-trading-bot

### Root Level
```
/opt/hybrid-trading-bot/
├── config.toml
├── Makefile
├── verify_phase3.sh
├── ws_sources.example.toml
└── ws_sources.toml
```

### Engine (Python - DEPRECATED)
```
engine/
└── Cargo.toml                    # Empty/stub
```

### Engine-Rust (ACTIVE)
```
engine-rust/
├── Cargo.toml
├── Cargo.lock
├── src/
│   ├── bin/mock_ws.rs
│   ├── config.rs
│   ├── db.rs
│   ├── execution.rs
│   ├── ingest/
│   │   ├── mod.rs
│   │   ├── realws.rs
│   │   ├── venuebook.rs
│   │   └── ws_sources.rs
│   ├── lib.rs
│   ├── main.rs
│   ├── persist.rs
│   ├── strategy.rs
│   └── types.rs
└── tests/
    ├── fixture_io.rs
    ├── pragma_verification.rs
    ├── property_tests.rs
    └── test_venuebook_normalization.rs
```

### Dashboard (Streamlit - ACTIVE)
```
dashboard/
├── app.py
├── home.py
├── lib/
│   └── db.py
└── .streamlit/
    └── config.toml
```

### Recorder
```
recorder/
├── __init__.py
├── journal_schema.py
├── shadow_artifacts.py
└── trade_journal.py
```

### Risk
```
risk/
├── __init__.py
├── eligibility.py
└── rules.py
```

### Strategies
```
strategies/
├── __init__.py
└── stale_edge.py
```

### Venues
```
venues/
├── __init__.py
└── kalshi.py
```

### Scripts
```
scripts/
├── capture_ws_frames.py
├── diagnose_cpu_pressure.sh
├── healthcheck.sh
├── port_guard_8501.sh
├── preflight_phase2.sh
├── proof_check.sh
├── proof_shadow.sh
├── proof_stall_detection.sh
├── proof_throttling_delta.sh
├── run_dashboard.sh
├── run_engine.sh
├── run_shadow_enhanced.py
├── run_shadow_prod_entrypoint.py
├── run_shadow_stale_edge.py
├── soak_2h.py
├── snapshot_health.sh
├── verify_phase2_1.sh
├── verify_phase2_proofs.sh
└── smoke/
    ├── gen_mock_shadow_artifacts.py
    ├── verify-prod-shadow-runner-wiring.sh
    └── verify-shadow-artifacts-ci.sh
```

### Tests
```
tests/
├── test_shadow_artifacts_contract.py
├── test_shadow_artifacts_no_secrets.py
└── test_stub_only_modules.py
```

### Data
```
data/
└── bot.db                        # SQLite database
```

---

## 3. Illegal/Obsolete References

### ILLEGAL PLATFORMS (CFTC Compliance Issues)

| File | Line | Reference | Status |
|------|------|-----------|--------|
| `venues/hyperliquid.py` | 14 | `self.base_url = "https://api.hyperliquid.com"` | ACTIVE CODE - ILLEGAL |
| `strategies/sef_spot_trading.py` | 4 | `Uniswap V3, dYdX, GMX V2` | Comment |
| `strategies/sef_spot_trading.py` | 37 | `# dYdX v4 and GMX V2 REMOVED` | Comment |
| `strategies/sef_spot_trading.py` | 49-50 | dYdX/GMX removed comments | Commented out |
| `strategies/sef_spot_trading.py` | 106-112 | Placeholder functions for dYdX/GMX | Dead code |
| `strategies/sef_spot_trading.py` | 261-262 | `prices["dydx"]`, `prices["gmx"]` | Active code calling REMOVED functions |
| `strategies/sef_spot_trading.py` | 279 | `if best_exchange in ["gmx"]` | Active code |
| `strategies/sef_spot_trading.py` | 440 | Log about REMOVED dYdX and GMX | Comment |
| `strategies/sef_extended_trading.py.REMOVED` | ALL | Full dYdX/GMX implementation | FILE MARKED REMOVED but still present |

### OBSOLETE APIs

| File | Line | Reference | Status |
|------|------|-----------|--------|
| `venues/predictit.py` | 13 | `self.base_url = "https://api.predictit.com/api"` | OBSOLETE - Still active in code |
| `run-arb.py` | 20 | `--venues` default includes `predictit` | ACTIVE - Used in cron |
| `strategies/stock_hunter.py` | 573 | `# marketaux: REMOVED - free tier exhausted` | Comment only |
| `.env` | 6 | `MARKETAUX_API_KEY` | CONFIG PRESENT but unused |

---

## 4. Systemd Services

| Service | Status | Description |
|---------|--------|-------------|
| `hybrid-engine.service` | **ACTIVE** | Rust trading engine |
| `hybrid-dashboard.service` | **ACTIVE** | Streamlit dashboard (port 8501) |
| `pm2-slimy.service` | **ACTIVE** | PM2 process manager |
| `admin-api.service` | active | Slimy Admin API |
| `admin-ui.service` | active | Slimy Admin UI |
| `slimy-backup-pull.service` | **FAILED** | Backup service (not trading related) |

---

## 5. Cron Jobs (slimy user)

### PM Bot Related
| Schedule | Command |
|----------|---------|
| `0 8 * * 0` | Weekly PNL report |
| `0 8,20 * * *` | run_with_monitoring.sh |
| `*/10 * * * *` | run-arb.py --venues kalshi,polymarket,predictit |

### Mission Control (ned-clawd)
| Schedule | Command |
|----------|---------|
| `*/5 * * * *` | heartbeat.sh |
| `0 8 * * *` | daily-briefing.sh |
| `* * * * *` | mc-comms-bot.sh |
| `0 * * * *` | check_pnl.sh |
| `*/15 * * * *` | watchdog.sh |
| `0 23 * * *` | nightly-memory-extract.sh |
| `@reboot, 0 * * * *` | register-agents.sh |
| `*/5 * * * *` | resource-monitor.py |
| `0 9 * * 1` | weekly-rotation-check.sh |
| `30 23 * * *` | decision-report.sh |

---

## 6. API Keys Configured (KEY NAMES ONLY)

From `.env`:
- `KALSHI_KEY` - **ACTIVE**
- `KALSHI_SECRET` - **ACTIVE**
- `FINNHUB_API_KEY` - Used in stock_hunter.py
- `ALPHA_VANTAGE_API_KEY` - Used in stock_hunter.py
- `MASSIVE_API_KEY` - Unknown usage
- `MARKETAUX_API_KEY` - **OBSOLETE** (free tier exhausted)
- `BINANCE_API_KEY` - Present but not used in active code
- `MEDIASTACK_API_KEY` - Unknown usage
- `WORLDNEWS_API_KEY` - Unknown usage
- `OPENWEATHER_API_KEY` - Unknown usage
- `WEATHERAPI_KEY` - Unknown usage
- `COINGECKO_API_KEY` - Unknown usage

---

## 7. Running Processes Related to Trading

| Process | Status | Notes |
|---------|--------|-------|
| `/opt/hybrid-trading-bot/engine-rust/target/release/engine-rust` | **RUNNING** | Rust trading engine |
| `streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501` | **RUNNING** | Dashboard on port 8501 |

---

## 8. Assessment: Active vs Dead Components

### ACTIVE COMPONENTS
1. **hybrid-engine.service** - Rust engine running
2. **hybrid-dashboard.service** - Streamlit dashboard on port 8501
3. **pm_updown_bot_bundle cron jobs** - Running arbitrage
4. **venues/polymarket.py** - Active Polymarket integration
5. **venues/kalshi.py** - Active Kalshi integration
6. **strategies/kalshi_optimize*.py** - Active optimization strategies
7. **utils/rotation_manager.py** - Active rotation management
8. **data/bot.db** - SQLite database in use

### DEAD/OBSOLETE COMPONENTS
1. **venues/hyperliquid.py** - Hyperliquid venue (ILLEGAL - CFTC compliance)
2. **venues/predictit.py** - PredictIt venue (OBSOLETE)
3. **strategies/sef_extended_trading.py.REMOVED** - Marked as REMOVED but still present
4. **strategies/sef_spot_trading.py** - Contains dead dYdX/GMX code paths
5. **MARKETAUX_API_KEY** - Configured but unused
6. **engine/Cargo.toml** - Empty/stub (replaced by engine-rust)

---

## 9. Recommended Removal List

### HIGH PRIORITY (Legal/Compliance)
| File | Reason | Action |
|------|--------|--------|
| `venues/hyperliquid.py` | Illegal - CFTC compliance | DELETE |
| `strategies/sef_spot_trading.py` lines 106-112, 261-262, 279 | dYdX/GMX dead code | CLEANUP |
| `strategies/sef_extended_trading.py.REMOVED` | Marked REMOVED but present | DELETE |

### MEDIUM PRIORITY (Obsolete)
| File | Reason | Action |
|------|--------|--------|
| `venues/predictit.py` | PredictIt not used | DELETE or archive |
| `run-arb.py` line 20 | Default includes predictit | UPDATE default venues |
| `.env` MARKETAUX_API_KEY | Free tier exhausted | REMOVE from .env |
| `strategies/stock_hunter.py` line 573 | marketaux reference | CLEANUP comment |

### LOW PRIORITY (Cleanup)
| File | Reason | Action |
|------|--------|--------|
| `engine/` directory | Deprecated (replaced by engine-rust) | DELETE if not needed |
| `proofs/backup_20260208_100000/` | Old backup from 2026-02-08 | ARCHIVE/DELETE |
| `proofs/polymarket-integration-*/` | Old integration proofs | ARCHIVE/DELETE |

---

## 10. Database Files

| Path | Size | Type |
|------|------|------|
| `/opt/hybrid-trading-bot/data/bot.db` | SQLite | Active |

---

## 11. Removal Log (2026-02-27)

### Removals Executed

| Date | File/Component | Action | Reason |
|------|----------------|--------|--------|
| 2026-02-27 | `venues/hyperliquid.py` | Replaced with stub | US-restricted perp DEX, illegal under CEA for US residents |
| 2026-02-27 | `venues/polymarket.py` | Replaced with stub | US invite-only, regulatory uncertainty |
| 2026-02-27 | `venues/predictit.py` | Replaced with stub | No bot API, 10% profit fee makes arbitrage unprofitable |
| 2026-02-27 | `strategies/sef_spot_trading.py` | Removed dYdX/GMX code | Unregistered derivatives, CFTC violation risk |
| 2026-02-27 | `strategies/sef_extended_trading.py.REMOVED` | Moved to backup | Superseded file |
| 2026-02-27 | `strategies/stock_hunter.py` | Commented marketaux | Free tier exhausted |
| 2026-02-27 | `.env` | Removed MARKETAUX_API_KEY | API no longer used |
| 2026-02-27 | `strategies/kalshi_optimize_fixed.py` | Moved to backup | Superseded |
| 2026-02-27 | `strategies/kalshi_optimize_fixed2.py` | Moved to backup | Superseded |
| 2026-02-27 | `strategies/kalshi_optimize_fixed3.py` | Moved to backup | Superseded |
| 2026-02-27 | `run-arb.py` | Updated default venues | Removed polymarket, predictit |

### Backup Location
All removed files moved to: `/opt/slimy/pm_updown_bot_bundle/.removed_20260227/`

### Verification Status
- No active hyperliquid references in Python files
- No active dYdX/GMX references in Python files
- No active marketaux references in Python files
- No active predictit references in Python files
- `runner` module imports successfully
- `stock_hunter` module imports successfully

---

*End of Audit Report*
