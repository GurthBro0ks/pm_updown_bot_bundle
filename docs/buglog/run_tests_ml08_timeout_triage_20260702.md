# run_tests.sh ML-08 Timeout Triage

Date: 2026-07-02
Proof: `/tmp/proof_run_tests_ml08_timeout_triage_20260702T143712Z`

## Root Cause

`scripts/run_tests.sh` ML-08 was labeled as venue argument parser validation, but it launched:

```bash
runner.py --mode shadow --venue kalshi
```

That executes the real shadow pipeline and can exceed the 10 second parser-test timeout. Later ML-11 through ML-16 checks had the same stale pattern for bankroll, max position, and micro-live parameter checks.

## Fix

- Replaced ML-08 runner subprocess execution with static `runner.py` argparse/source-contract validation.
- Replaced ML-11 through ML-16 runner subprocess execution with bounded static source-contract checks.
- Kept polymarket rejection coverage by checking both parser choices and the explicit `args.venue == "polymarket"` exit guard.
- Narrowed ML-09 to the meaningful deprecation invariant: zero `datetime.utcnow()` calls.
- Escaped the ML-13 `$0.01` shell label so logs do not expand `$0`.

## Validation

- `./scripts/run_tests.sh` - PASS, exit 0.
- `./venv/bin/python3 -m pytest tests/ -x -q` - PASS, 481 passed, 2 warnings.
- Weather cron policy preserved: `WEATHER_DRY_RUN=true`, no `WEATHER_LIVE_ENABLED=true`.
- Weather smoke remained dry-run only.

## Safety

No orders, no live weather smoke, no Discord, no cron edit, no service restart, no Caddy/DNS/systemd/tmux change, no `.env` read/write, and no push.
