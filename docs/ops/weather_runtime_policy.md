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
- `scripts/run_weather_strategy.py` runs dry-run unless `WEATHER_DRY_RUN=false`, `WEATHER_LIVE_ENABLED=true`, and `--dry-run` is absent.

Required preservation paths for future strategy cleanup:

- `scripts/run_weather_strategy.py`
- `scripts/cron_weather_trade.sh`
- Weather-specific safety environment variables.
- Weather `record_trade` markers: `market_category` and `signal_type`.
- Weather Discord formatting, without printing webhook values.
- Main live order client path.
- AI calibration path.
- Runner `phase-all` path.
