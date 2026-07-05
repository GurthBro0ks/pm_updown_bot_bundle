# pm_redacted_health_direct_invocation_fix

Date: 2026-07-03
Result: PASS

## Issue

`scripts/kalshi_redacted_health_check.py` imported repo-local modules before
ensuring the repository root was on `sys.path`. Running the script directly as
`python3 scripts/kalshi_redacted_health_check.py` from the repo root could fail
unless the caller supplied `PYTHONPATH=.`.

## Fix

Added a small `__file__`-based repository root bootstrap near the top of the
script, before importing `config` or repo-local utilities. The script now
inserts the repo root into `sys.path` only when it is not already present.

## Validation

- `python3 scripts/kalshi_redacted_health_check.py`: PASS
- `PYTHONPATH=. python3 scripts/kalshi_redacted_health_check.py`: PASS
- `PYTHONPATH=. pytest tests/test_kalshi_redacted_health_check.py`: PASS, 8 passed
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`
- Sanitized weather cron posture check: dry-run locked, `WEATHER_LIVE_ENABLED=true` absent

## Safety

- No `.env`, key files, PEMs, auth headers, webhook configs, or credential
  values were read or printed.
- No orders were placed or canceled.
- Cron was read only through a sanitized boolean check and was not changed.
- Weather live was not armed.
