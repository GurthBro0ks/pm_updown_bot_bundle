# PM Main Three-Day Fetch Universe Supplement Fix

Date: 2026-07-08
Phase: `pm_main_three_day_fetch_universe_supplement_fix`

## Problem

The main bot preserved `MAX_DAYS_TO_EXPIRY=3`, but its Kalshi fetch universe
was selected from the top `KALSHI_SERIES_LIMIT=50` series by volume. A read-only
comparison showed eligible short-horizon allowed-category markets existed in
`KXNASDAQ100U`, but that series ranked far outside the top-N selection and was
therefore missing before the normal expiry/category/weather gates could evaluate
it.

## Fix

Added a bounded default priority supplemental series list containing
`KXNASDAQ100U`. The fetch path still selects the normal top-N series, then
dedupes and appends only the explicit supplemental series if it was missed.
Markets keep a `kalshi_fetch_source` marker so the redacted inventory can report
how many candidates came from the supplemental path.

## Safety

- `MAX_DAYS_TO_EXPIRY` remains 3.
- Allowed categories were not expanded.
- Weather state and cron were not changed.
- Existing expiry, category, weather, price, risk, open-order, and exposure
  gates still apply after fetch.
- No main live run, order placement, order cancellation, service restart, or
  secret print occurred during validation.

## Validation

- `python3 -m py_compile utils/kalshi.py scripts/main_market_inventory_redacted.py tests/test_kalshi_fetch_supplemental_series.py tests/test_main_market_inventory_redacted.py`: PASS.
- `PYTHONPATH=. pytest tests/test_kalshi_fetch_supplemental_series.py tests/test_main_market_inventory_redacted.py -q`: PASS, 16 passed.
- `python3 scripts/main_market_inventory_redacted.py --max-days 3 --allowed-categories index,crypto,economics,commodities,financials`: PASS, `MAX_DAYS=3`, `THREE_DAY_ALLOWED_COUNT=105`, `SUPPLEMENTAL_THREE_DAY_ALLOWED_COUNT=100`.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS.
- `PYTHONPATH=. pytest tests -q`: PASS, 544 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
