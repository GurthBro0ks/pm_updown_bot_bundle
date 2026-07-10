# Main Intent-to-Submission Diagnostic Gap

Date: 2026-07-10

## Finding

`main_run_gate_summary_redacted.py` defaulted to `logs/cron.log`, while the
installed main micro-live cron wrapper writes to `logs/cron_micro_live.log`.
The gate tool therefore selected the `08:00Z` artifact, while
`main_edge_nearest_miss_redacted.py` read the current structured summary from
`logs/main_edge_nearest_miss_latest.json` for the `12:00Z` run.

The prior `ORDER_SUBMISSION_PROCESSED` field also reflected a stage-budget
counter rather than a count of attempted order submissions. It cannot prove a
post-intent blocker.

## Diagnostic-Only Change

- Gate summary now defaults to the main micro-live log and prints its artifact.
- Nearest-miss output prints its structured-summary artifact.
- Future micro-live summaries emit redacted post-intent blocker counts,
  intent-to-submission status, and submission skip reasons.

No trading policy, price/category/expiry gate, cron, weather setting, order
action, or service state changed.

## Validation

- `python3 -m py_compile ...` passed for all touched Python modules.
- Focused diagnostics tests: 19 passed.
- `PYTHONPATH=. pytest tests`: 567 passed, 1 known deprecation warning.
- `./scripts/run_tests.sh`: `STATUS: ALL GATES PASS`.
