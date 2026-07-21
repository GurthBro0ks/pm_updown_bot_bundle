# Expanded Shadow Scanner one-shot observation contract

## Purpose

Use one atomic, redacted status record to observe one expected Expanded Shadow
Scanner invocation. The contract is observational only: it is not an input to
market selection, priors, gates, sizing, pricing, orders, or scanner exit
behavior.

The scanner writes a start record before fetching markets and replaces it with
one terminal record after a normal return or an exception. Each record belongs
to one exact run ID and expected UTC schedule. The reader opens only that
run's bounded record. It does not scan logs, infer activity from appended
bytes, expand a date-bearing log path, inspect spool payloads, or poll.

## Activation-time inputs

Natural scheduled observation now uses the dynamic per-invocation contract in
`docs/ops/expanded_shadow_dynamic_attribution.md`. It stays disabled unless the
stable dynamic enable flag and stable absolute status root are set. The run ID,
expected schedule, and final status path are derived from the UTC process start
for every invocation.

The original explicit inputs remain supported for disposable synthetic tests:

- `EXPANDED_SHADOW_RUN_ID`: a unique safe identifier, normally derived from the
  expected UTC schedule, such as `expanded-shadow-YYYYMMDDTHHMMSSZ`.
- `EXPANDED_SHADOW_EXPECTED_SCHEDULED_AT`: the exact ISO-8601 UTC schedule.
- `EXPANDED_SHADOW_RUN_STATUS_DIR`: an explicit owner-only directory under the
  ignored runtime tree.

Candidate capture remains independently disabled by default. Enabling run
observation does not enable candidate capture.

Do not install those explicit per-run fields for a natural recurring
activation. A static binding reuses stale attribution on later runs.

For an approved future activation, place capture and observation assignments
after `/usr/bin/env` and before `/usr/bin/timeout`. Do not place an assignment
where `timeout` will interpret it as the executable. Validate the generated
entry structurally and with a disposable synthetic command before installing
it. Never infer correct placement from the mere presence of field names.

## Reader

Invoke `scripts/expanded_shadow_run_status_redacted.py` once with either the
legacy explicit triple or the stable `--status-root` dynamic lookup. An
activation observer also supplies `--require-capture-mode spool`. The command
prints a fixed 23-field machine-readable record and exits immediately.

Status meanings:

- `NOT_STARTED`: the exact expected record is absent.
- `RUNNING`: the exact record contains a fresh start and no terminal state.
- `PASS`: normal exit zero, attributable timestamps, no capture warnings or
  drops, and any required capture mode matches.
- `WARN`: stale status, successful scanner completion with capture warnings or
  drops, or required capture mode mismatch.
- `FAIL`: explicit scanner exception, invalid record, or attribution mismatch.

A normal zero-market or zero-candidate run is `PASS` with zero counts. Missing
status never becomes success. A record for another run ID or scheduled time is
rejected. Status remains valid if the ordinary scanner log rotates or is
truncated.

## Future unattended runbook

Use the precise activation and rollback runbooks in
`docs/ops/expanded_shadow_dynamic_attribution.md`. They require stable fields,
one-shot observation, fail-safe rollback, and a hard sunset without per-run cron
rebinding.

No long-running model monitoring, log-tail polling, raw log inspection, raw
spool inspection, production SQLite access, or external notification is part of
the observation decision.
