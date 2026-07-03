# PM Weather Live Activation Guarded

Date: 2026-07-03

## Summary

Weather live mode had existing weather-specific limits, but they were not small
enough for the guarded activation target and did not fail closed when tiny live
limit variables were missing. The weather runner now requires explicit live
limits and enforces fixed hard caps before using the live order path.

## Fix

- Require explicit live weather caps for `WEATHER_MAX_ORDERS_PER_RUN`,
  `WEATHER_MAX_ORDER_USD`, `WEATHER_MAX_RUN_EXPOSURE_USD`, and
  `WEATHER_MAX_OPEN_EXPOSURE_USD`.
- Fail closed if any required live cap is missing, malformed, non-finite,
  non-positive, or above the hard-coded safe cap.
- Enforce max $0.25/order, max $1.00/run exposure, max $2.00 open weather
  exposure, and max 1 live weather order/run.
- Preserve dry-run behavior and keep the main micro-live trading path untouched.

## Validation

- `bash -n scripts/cron_weather_trade.sh`: PASS.
- `./venv/bin/python3 -m pytest tests/test_weather_live.py -q`: PASS,
  20 passed.
- `./venv/bin/python3 -m pytest tests/ -q -k 'weather or breaker or circuit or calibration'`:
  PASS, 82 passed, 415 deselected, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- `./venv/bin/python3 -m pytest tests/ -q`: PASS, 497 passed, 2 warnings.
- Weather dry-run smoke with tiny limits: PASS dry-run only, 0 trades placed.

## Safety

Installed cron was not changed and no live weather cycle was executed because
cron mutation and trading/order actions require a fresh exact-bounded nonce
approval block under the host policy. Weather cron remains dry-run locked.
