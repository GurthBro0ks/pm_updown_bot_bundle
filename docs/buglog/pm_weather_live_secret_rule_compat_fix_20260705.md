# pm_weather_live_secret_rule_compat_fix_20260705

## Summary

Fixed two source-level blockers found during the weather live arming dry-run:

- Repo validation now documents `/home/slimy/init.sh` as the required bootstrap,
  with repo-local `init.sh` optional only if present.
- `scripts/cron_weather_trade.sh` no longer parses `.env` with `grep | xargs`.
  It resolves the repo root from the script path, sources the runtime env file
  with auto-export, disables xtrace around loading, and prints only variable
  names on fail-closed validation errors.

## Safety

- No installed cron change.
- No weather live arming.
- No live weather run.
- No order placement or cancellation.
- No service restart.
- No secret values printed.

## Validation

See proof directory for exact command outputs:

- `bash -n scripts/cron_weather_trade.sh`
- `python3 scripts/kalshi_redacted_health_check.py`
- Focused weather/env tests
- `PYTHONPATH=. pytest tests`
- `./scripts/run_tests.sh`
- Weather dry-run smoke
- Sanitized cron check
