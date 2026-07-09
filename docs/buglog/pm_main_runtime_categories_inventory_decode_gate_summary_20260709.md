# PM Main Runtime Category, Inventory Decode, and Gate Summary Fix

Date: 2026-07-09

## Issue

The read-only post-inventory diagnosis found three quality gaps:

- Runtime `.env` loading needed explicit test coverage proving inline `KALSHI_ALLOWED_CATEGORIES` survives fake env defaults.
- The redacted inventory command could classify a Kalshi response decode failure as zero fetched markets.
- Latest main-run zero-order diagnosis required a redacted gate summary that distinguishes price-floor skips from profitability/edge no-intent runs.

## Fix

- Added cron wrapper tests proving inline `KALSHI_ALLOWED_CATEGORIES=index,crypto,economics,commodities,financials` overrides a fake env default containing `politics`.
- Made `scripts/main_market_inventory_redacted.py` request `Accept-Encoding: identity` for its read-only fetch path and classify decode failures as `WARN_DECODE_UNSUPPORTED` with `ZERO_CANDIDATE_REASON=decode_unsupported_fail_closed`.
- Added `scripts/main_run_gate_summary_redacted.py`, a read-only log parser that emits only safe counts and zero-order classifications.

## Verification

- `bash -n scripts/cron_micro_live.sh`
- `bash -n scripts/cron_weather_trade.sh`
- `python3 -m py_compile scripts/main_market_inventory_redacted.py scripts/main_run_gate_summary_redacted.py`
- `PYTHONPATH=. pytest -q tests/test_cron_micro_live_env.py tests/test_main_market_inventory_redacted.py tests/test_main_run_gate_summary_redacted.py`
- `python3 scripts/main_market_inventory_redacted.py --max-days 3 --allowed-categories index,crypto,economics,commodities,financials`
- `python3 scripts/kalshi_redacted_health_check.py`
- `python3 scripts/main_run_gate_summary_redacted.py --log logs/cron.log`
- `PYTHONPATH=. pytest tests`
- `./scripts/run_tests.sh`
