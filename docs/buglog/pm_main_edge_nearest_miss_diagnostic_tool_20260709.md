# Main Edge Nearest-Miss Diagnostic Tool

Date: 2026-07-09
Type: Diagnostic tooling only

## Problem

The redacted main-run gate summary could classify zero-order runs as
`edge_or_profitability_blocked`, but it could not show safe per-candidate
nearest-miss details. That made it hard to tell whether candidates narrowly
missed the fee-adjusted edge threshold, failed a price gate, or were blocked by
another downstream gate without inspecting raw logs.

## Change

- Added `utils/edge_nearest_miss.py` for whitelisted public nearest-miss records
  and structured redacted summary formatting.
- Added `scripts/main_edge_nearest_miss_redacted.py`, a read-only direct CLI.
- Added a future-run summary writer in `strategies/kalshi_optimize.py` that
  records public ticker/category/side/price/prior/edge/threshold/reason values.
- Added focused tests for missing-summary WARN behavior, redaction, bounded
  samples, direct invocation, and no order placement on edge-blocked candidates.

## Safety

No trading thresholds, edge gates, price gates, Kelly sizing, bankroll/notional
limits, category filters, max-days settings, cron, weather state, services, or
order placement paths were changed. Current runs without a structured summary
return `WARN_NO_PRIOR_STRUCTURED_DETAIL`; the next scheduled main run should
populate the latest structured summary.
