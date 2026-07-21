# Phase 1C observer completion and telemetry contract repair

Date: 2026-07-21

## Symptom

The unattended observer rolled candidate capture back at 18:15 UTC because it
could not find a normal completion marker or capture summary for the expected
18:00 Expanded Shadow Scanner invocation.

## Independently proven cause

The date-expanded target log was correct, but the 18:00 invocation never
entered Python. Exactly one bounded post-baseline shell error classified the
capture-enabled assignment as a command that was not found. There was no 18:00
scanner start, fetch, capture summary, exception, or exit marker. The next
ordinary disabled runs at 20:00 and 22:00 completed normally.

The observer also had an attribution defect: `SCHEDULED_RUN_STARTED` meant only
that some post-baseline bytes existed. The shell error therefore became a false
start. Completion and telemetry were found by scanning an arbitrary log suffix,
with no invocation ID or expected-schedule binding. The root-cause class is
`MULTIPLE_CAUSES`: `SCANNER_DID_NOT_RUN` plus `RUN_ATTRIBUTION_GAP`. This was not
a zero-event telemetry gap; the repaired no-markets scanner already emits its
complete zero-valued capture summary and exit zero marker.

## Repair

- Add a disabled-by-default, per-invocation atomic status writer with explicit
  run ID, expected UTC schedule, start, terminal completion/failure, canonical
  return counts, capture mode, bounded event counts, warnings, and drops.
- Add a one-shot redacted reader that opens only the expected run record,
  rejects stale or mismatched attribution, and never scans logs or payloads.
- Extend existing capture summary fields with truthful per-event-type counts.
  Order attempt/result remain zero because Phase 1B capture does not invent
  those events.
- Preserve scanner exceptions by writing a redacted failure record and
  re-raising. Observation failures remain isolated from scanner behavior.
- Document `/usr/bin/env` placement for any future approved activation so
  assignments cannot be interpreted as the timeout executable.

## Production posture

This repair does not activate capture or status output by default, alter
installed cron, schedule ingest, create a production database, inspect spool
payloads, change trading/weather behavior, or perform a live run. A future
activation retry requires independent review and fresh approval.
