# PM Main Micro-Live Gate Failure Kind Diagnostic - 2026-07-09

## Summary

Added redacted diagnostic-only gate failure kinds to the main nearest-miss edge
summary so a candidate that passes visible price and fee-adjusted edge checks no
longer collapses to only `rejection_reason=gate_failed`.

## Root Cause

`gate_failed` was a generic diagnostic label used when
`check_micro_live_gates()` returned false for a non-edge violation. The
structured nearest-miss payload did not include the underlying violation class,
so operators could not distinguish fallback prior, size, liquidity, market end
time, price sanity, or unknown future gate failures from the redacted tool.

## Change

- Added a diagnostic-only classifier for existing `check_micro_live_gates()`
  violation strings.
- Preserved the existing `(passed, violations)` gate return type and all
  pass/fail decisions.
- Added `gate_failure_kind`, `gate_failure_kinds`, and
  `gate_failure_kind_counts` to the redacted nearest-miss structured output.
- Updated the redacted CLI output to include `GATE_FAILURE_KIND_COUNTS=` and
  `gate_failure_kind` in public nearest-miss samples.

## Safety

No trading thresholds, gates, sizing, order placement, cron, weather state, or
runtime configuration were changed.
