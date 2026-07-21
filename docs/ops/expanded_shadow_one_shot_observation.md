# Expanded Shadow Scanner one-shot observation contract

## Purpose

Use one atomic, redacted status record to observe one expected Expanded Shadow
Scanner invocation. The contract is observational only: it is not an input to
market selection, priors, gates, sizing, pricing, orders, or scanner exit
behavior.

The scanner writes a start record before fetching markets and replaces it with
one terminal record after a normal return or an exception. Each record belongs
to one explicit run ID and expected UTC schedule. The reader opens only that
run's bounded record. It does not scan logs, infer activity from appended
bytes, expand a date-bearing log path, inspect spool payloads, or poll.

## Activation-time inputs

Run observation stays disabled unless all three variables are explicitly set:

- `EXPANDED_SHADOW_RUN_ID`: a unique safe identifier, normally derived from the
  expected UTC schedule, such as `expanded-shadow-YYYYMMDDTHHMMSSZ`.
- `EXPANDED_SHADOW_EXPECTED_SCHEDULED_AT`: the exact ISO-8601 UTC schedule.
- `EXPANDED_SHADOW_RUN_STATUS_DIR`: an explicit owner-only directory under the
  ignored runtime tree.

Candidate capture remains independently disabled by default. Enabling run
observation does not enable candidate capture.

For an approved one-shot activation, place capture and observation assignments
after `/usr/bin/env` and before `/usr/bin/timeout`. Do not place an assignment
where `timeout` will interpret it as the executable. Validate the generated
entry structurally and with a disposable synthetic command before installing
it. Never infer correct placement from the mere presence of field names.

## Reader

Invoke `scripts/expanded_shadow_run_status_redacted.py` once with an explicit
status directory, expected run ID, and expected UTC schedule. An activation
observer also supplies `--require-capture-mode spool`. The command prints a
fixed 23-field machine-readable record and exits immediately.

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

## Future unattended retry runbook (do not execute during source repair)

1. Obtain fresh exact-bounded approval for the cron mutation. Reconfirm a clean
   accepted repository, disabled capture in every lane, no scheduled offline
   ingest, baseline sanitized cron fingerprint, empty owner-only status target,
   and owner-only spool parent.
2. Calculate the next natural direct Expanded Shadow Scanner schedule in UTC.
   Create exactly one run ID from that timestamp and an owner-only status
   directory. Do not use a date-expanded log path as the observation source.
3. Build a candidate crontab in memory that changes only the direct Expanded
   Shadow Scanner entry. Add candidate-capture and observation variables via
   `/usr/bin/env` before the existing timeout executable. Leave micro-live,
   phase-all, weather, multi-live, and offline ingest unchanged and disabled
   for capture.
4. Add three date-gated one-shot control entries: an observer after the natural
   run, an independent fail-safe after the observer, and a hard sunset. Each
   control action must be locked, idempotent, self-removing, preserve spool
   contents, and restore the exact baseline fingerprint on rollback.
5. Before installation, verify shell/Python syntax, exact field placement,
   one direct scanner entry, one capture-enabled lane, expected status target,
   baseline-to-candidate structural diff, and a disposable synthetic
   `/usr/bin/env` invocation. Run the rollback calculation against the candidate
   bytes and require exact baseline recovery.
6. Install only after the approval and preflight gates pass. Do not run the
   scanner, trading, weather, or ingest manually; do not start a monitoring
   process or polling loop.
7. The observer invokes the redacted reader exactly once for the expected run
   ID and schedule with `--require-capture-mode spool`. It accepts `PASS`,
   including truthful zero-market and zero-candidate completion. It rolls back
   on `NOT_STARTED`, `RUNNING`, `WARN`, `FAIL`, reader error, attribution
   mismatch, capture warning/drop, SQLite presence, isolation failure, or cron
   fingerprint mismatch.
8. The fail-safe rolls back unless an attributable observer PASS record exists.
   The sunset rolls back unless a separately approved keep decision exists.
   Neither path deletes or inspects spool payloads.
9. After the one-shot result, verify capture disabled if rolled back, the exact
   baseline fingerprint, absence of temporary entries, no production database,
   no automated ingest, and unchanged trading/weather behavior. Independent
   review and fresh approval remain required before any further activation.

No long-running model monitoring, log-tail polling, raw log inspection, raw
spool inspection, production SQLite access, or external notification is part of
the observation decision.
