# pm_main_three_day_redacted_market_inventory_tool_20260708

## Summary

Added a read-only redacted inventory tool for the main Kalshi bot so operators
can see why the 3-day main strategy has zero public non-weather candidates
without running a live cycle or printing credentials.

## Safety Contract

- The tool fetches public market metadata through the existing read-only market
  fetch path.
- It does not place, cancel, or modify orders.
- It does not inspect or print credential values, auth headers, key paths, or
  response bodies.
- It defaults to `MAX_DAYS=3` and refuses attempts to widen `--max-days`.
- Category allowlist defaults are tracked source/CLI values, not env-derived.

## Output

The tool prints fixed key/value lines:

- `MARKET_INVENTORY`
- `VALUES_PRINTED`
- `MAX_DAYS`
- `TOTAL_FETCHED`
- `AFTER_EXPIRY_FILTER`
- `AFTER_WEATHER_EXCLUSION`
- `AFTER_CATEGORY_FILTER`
- `CATEGORY_COUNTS_PUBLIC`
- `THREE_DAY_ALLOWED_COUNT`
- `THREE_DAY_DISALLOWED_COUNT`
- `ZERO_CANDIDATE_REASON`
- public ticker samples for allowed and disallowed candidates

## Validation

- Focused tests cover allowed-category survival, long-expiry exclusion, weather
  exclusion, disallowed-category counting, zero-candidate reasons, no fake
  secret-looking output, direct invocation without `PYTHONPATH`, and max-day
  widening refusal.
- Live read-only inventory on 2026-07-08 returned
  `ZERO_CANDIDATE_REASON=no_current_3_day_markets`.
