# pm_main_cron_preserve_inline_max_days_fix_20260706

## Summary

The main micro-live cron wrapper loaded the repo env after operator inline cron
overrides. That allowed env defaults to replace the intended
`MAX_DAYS_TO_EXPIRY=3` value, and recent runtime logs showed the strategy still
using `max_days=14`.

## Fix

`scripts/cron_micro_live.sh` now snapshots known non-secret runtime overrides
before sourcing the env file, loads the env with xtrace disabled, and restores
those runtime overrides afterward. The preserved knobs are limited to main bot
runtime controls, including `MAX_DAYS_TO_EXPIRY`, and do not include credential
fields.

## Validation

- `bash -n scripts/cron_micro_live.sh`: PASS
- `bash -n scripts/cron_weather_trade.sh`: PASS
- `PYTHONPATH=. pytest tests/test_cron_micro_live_env.py tests/test_cron_weather_env.py -q`: PASS, 9 passed
- `python3 scripts/kalshi_redacted_health_check.py`: PASS, redacted readonly health only
- `PYTHONPATH=. pytest tests -q`: PASS, 528 passed, 1 warning
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`

## Safety

No real env file was inspected. No installed cron was changed. No main live run,
order placement/cancellation, category-gate change, weather arm/disarm, service
restart, Caddy/DNS/systemd/timer/tmux change, or Discord notification occurred.

## Remaining

Operator QA is pending until the next scheduled main cron confirms the runtime
log uses `max_days=3`.
