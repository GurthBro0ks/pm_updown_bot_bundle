# pm_kalshi_redacted_health_canary_refresh

Date: 2026-07-03

## Issue

Kalshi credential rotation verification could not return a clean PASS because
there was no purpose-built redacted readonly health command for portfolio and
open orders. The canary also depended on one configured market ticker, so a
stale ticker could make the pipeline dry-run fail even when Kalshi auth/fetch
was healthy.

## Fix

- Added `scripts/kalshi_redacted_health_check.py`, which signs readonly GET
  requests and prints only PASS/WARN/FAIL labels, a coarse HTTP status class,
  and `VALUES_PRINTED=no`.
- Added portfolio balance and resting open-orders readonly checks without
  printing env values, key IDs, key paths, headers, or response bodies.
- Updated the canary pipeline dry-run to prefer the configured ticker but fall
  back to a currently fetched usable market when the configured ticker is stale.
- Added focused tests for redacted health output, readonly status handling, and
  stale canary ticker fallback.

## Validation

- `bash -n scripts/cron_weather_trade.sh`: PASS.
- `python3 -m py_compile scripts/kalshi_redacted_health_check.py core/canary.py tests/test_kalshi_redacted_health_check.py tests/test_canary.py`: PASS.
- `PYTHONPATH=. pytest tests/test_kalshi_redacted_health_check.py tests/test_canary.py -q`: PASS, 37 passed.
- `PYTHONPATH=. pytest tests`: PASS, 519 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Redacted health command: PASS for auth, portfolio, and open-orders; HTTP status class 2xx.
- Sanitized canary run: PASS for provider health, Kalshi auth/fetch, and pipeline dry-run.
- Weather dry-run smoke: PASS, 0 trades placed.
- Sanitized cron check: weather dry-run true, `WEATHER_LIVE_ENABLED` absent.

## Safety

No live order placement, order cancellation, weather live arm, cron change,
service restart, Caddy/DNS/systemd/timer/tmux change, secret print, or webhook
notification.
