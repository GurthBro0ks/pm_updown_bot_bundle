# proof_snapshot_diff_template_fix

Date: 2026-07-02

## Bug

Some mission proof templates compared full before/after proof files even when
the first rows were intentionally different decorative labels, such as
`=== BEFORE ===` and `=== AFTER ===`. That can report a false failure even when
the protected hash rows are unchanged.

## Fix

Added `scripts/proof_compare.py`, a normalized proof comparison helper that
ignores decorative `===` header rows by default and prints only result,
normalized SHA256 values, and row counts unless an operator explicitly requests
`--show-diff`.

## Tests

- `./venv/bin/python3 -m pytest tests/test_proof_compare.py -q`
- `./scripts/run_tests.sh`
- `./venv/bin/python3 -m pytest tests/ -x -q`
- `WEATHER_DRY_RUN=true timeout 90 ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run`

## Result

PASS. The false-diff template issue is fixed without changing trading logic or
production behavior.
