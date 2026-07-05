# Weather Runtime Policy

Current policy: **weather trading is live-armed with tiny weather-only caps**.

Weather was promoted to live after these gates passed:

- Security cleanup PASS.
- Exposed Kalshi credentials rotated and old key revoked.
- Full pytest PASS.
- Breaker-state tests PASS.
- Operator manual QA PASS.
- Claude safety closeout PASS.
- A separate live-weather activation prompt is approved.

Current accepted installed weather cron state:

- `WEATHER_DRY_RUN=false`
- `WEATHER_LIVE_ENABLED=true`
- `WEATHER_MAX_ORDERS_PER_RUN=1`
- `WEATHER_MAX_ORDER_USD=0.25`
- `WEATHER_MAX_RUN_EXPOSURE_USD=1.00`
- `WEATHER_MAX_OPEN_EXPOSURE_USD=2.00`

Rollback target, if any future WARN/FAIL or operator decision requires it:

- `WEATHER_DRY_RUN=true`
- `WEATHER_LIVE_ENABLED` absent or false

Main bot cron must not be changed by weather policy work.

Both weather entry points fail closed:

- `scripts/cron_weather_trade.sh` passes `--dry-run` unless `WEATHER_DRY_RUN=false` and `WEATHER_LIVE_ENABLED=true`.
- `scripts/cron_weather_trade.sh` loads runtime environment through `ENV_FILE`/`.env` using shell `source` with auto-export and xtrace disabled around loading. It must never use grep/xargs env parsing, echo loaded values, or require agents to inspect `.env`.
- `scripts/run_weather_strategy.py` runs dry-run unless `WEATHER_DRY_RUN=false`, `WEATHER_LIVE_ENABLED=true`, and `--dry-run` is absent.

Startup validation for future live-weather arming phases should require the host
bootstrap (`source /home/slimy/init.sh`). This repo does not require a local
`init.sh`; if one exists, it may be sourced after the host bootstrap.

Required preservation paths for future strategy cleanup:

- `scripts/run_weather_strategy.py`
- `scripts/cron_weather_trade.sh`
- Weather-specific safety environment variables.
- Weather `record_trade` markers: `market_category` and `signal_type`.
- Weather Discord formatting, without printing webhook values.
- Main live order client path.
- AI calibration path.
- Runner `phase-all` path.
