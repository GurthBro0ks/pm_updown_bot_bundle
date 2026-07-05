# Weather Runtime Policy

Current policy: **weather trading stays DRY-RUN**.

Weather may only be promoted to live after all gates are true:

- Security cleanup PASS.
- Exposed Kalshi credentials rotated and old key revoked.
- Full pytest PASS.
- Breaker-state tests PASS.
- Operator manual QA PASS.
- Claude safety closeout PASS.
- A separate live-weather activation prompt is approved.

During this blocked state, weather cron must use `WEATHER_DRY_RUN=true`.
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
