# Expanded Shadow Scanner dynamic run attribution

## Scope and production posture

This contract attributes every natural direct Expanded Shadow Scanner invocation
without changing installed cron for each run. It is source-only and disabled by
default. It does not enable candidate capture, run the scanner, schedule ingest,
or alter market discovery, filtering, priors, gates, sizing, prices, orders,
weather, or the main trading lane.

The selected design is an optional mode in the existing scanner CLI and
`research.candidate_ledger.run_observation` module. It avoids an additional
shell wrapper and keeps scanner arguments, exit status, and signal behavior on
the existing direct entrypoint.

## Natural schedule contract

- Schedule identifier: `utc-even-hours-v1`.
- Timezone: UTC only.
- Cadence: minute zero of every even UTC hour (`0 */2 * * *` in the verified
  operational schedule).
- Default scanner startup grace: 900 seconds, inclusive.
- Configurable startup grace: 0 through 1800 seconds. The optional stable
  `EXPANDED_SHADOW_SCHEDULE_GRACE_SECONDS` field changes this bound; production
  adoption should keep the reviewed 900-second default unless separately
  reviewed.
- Slot selection: floor the UTC process observation-init time to the preceding
  even-hour boundary. Never round forward.
- Expected timestamp: canonical ISO-8601 UTC, for example
  `2026-07-21T16:00:00Z`.
- Run ID: `expanded-shadow-YYYYMMDDTHHMMSSZ`, for example
  `expanded-shadow-20260721T160000Z`.

An invocation at the exact slot, seconds after it, or no more than 900 seconds
late belongs to that slot. The 900-second boundary is accepted; 901 seconds is
rejected. A process started immediately before a slot remains associated with
the preceding slot calculation and is rejected as too late rather than rounded
into the future. Odd-hour and other out-of-window starts are `UNSCHEDULED`.
UTC conversion occurs before slot calculation, so host local time and daylight
saving transitions cannot change attribution.

## Stable activation inputs

Future production activation needs only these stable observation fields on the
existing direct Expanded Shadow Scanner entry:

```text
EXPANDED_SHADOW_DYNAMIC_ATTRIBUTION=true
EXPANDED_SHADOW_RUN_STATUS_ROOT=/opt/slimy/pm_updown_bot_bundle/runtime/expanded_shadow_run_status
```

The status root is a stable absolute owner-only directory. Candidate capture
continues to use its own independently reviewed stable enable flag and spool
path. Dynamic attribution does not enable capture.

Do not install any of these legacy per-run fields for natural dynamic runs:

```text
EXPANDED_SHADOW_RUN_ID
EXPANDED_SHADOW_EXPECTED_SCHEDULED_AT
EXPANDED_SHADOW_RUN_STATUS_DIR
```

The legacy explicit three-field form remains available only for disposable
synthetic tests and bounded manual QA. Dynamic and explicit attribution cannot
be combined; the CLI fails closed before scanner logic if both are supplied.

## Status and concurrency contract

For each natural slot, the stable root contains:

```text
<status-root>/expanded-shadow-YYYYMMDDTHHMMSSZ.json
<status-root>/expanded-shadow-YYYYMMDDTHHMMSSZ.claim
```

The JSON record remains the bounded, redacted v1 STARTED / COMPLETED / FAILED
contract. It is written to an owner-only temporary file, flushed, fsynced, and
atomically replaced. STARTED cannot be read as COMPLETED. Terminal writes keep
the scanner exit code and preserve exceptions as FAILED without including the
exception message, raw traceback, candidates, venue data, credentials, or
spool payloads.

The persistent owner-only claim is created with exclusive filesystem creation
before STARTED. It is deliberately retained with the status record:

- A second process for a RUNNING run is `DUPLICATE_RUNNING` and exits before
  scanner logic.
- A duplicate after COMPLETED is `DUPLICATE_COMPLETED`; the successful record
  is not overwritten.
- A duplicate after FAILED is `DUPLICATE_FAILED`; failure remains visible.
- A claim older than six hours without a valid status is `STALE_CLAIM` and is
  not automatically removed or reclaimed.
- A malformed, oversized, mismatched, non-file, or symlink status is
  `MALFORMED_EXISTING_STATUS` and is not overwritten.

All duplicate and malformed cases fail closed. There is no polling, waiting,
automatic retry, or status/claim deletion. Distinct natural slots have distinct
IDs, so one run's retained claim cannot block the next scheduled run.

At two-hour cadence, the root grows by twelve small JSON records and twelve
empty claim markers per day. Retention cleanup is deferred because no deletion
is authorized in this phase. A future retention policy must be separately
reviewed and must never participate in scanner execution.

## Manual and unscheduled behavior

| Invocation | Result |
|---|---|
| Exact even-hour UTC slot | Derived natural attribution |
| Seconds or bounded minutes after the slot | Same derived natural attribution |
| Exactly 900 seconds late | Derived natural attribution |
| More than 900 seconds late | `UNSCHEDULED`; scanner CLI exits 2 before scan |
| Immediately before the next slot | Preceding slot is too old; `UNSCHEDULED` |
| Odd hour | `UNSCHEDULED`; scanner CLI exits 2 before scan |
| Explicit disposable three-field override with dynamic mode off | Synthetic attribution |
| Dynamic attribution off and no explicit override | Observation disabled; legacy scanner behavior |

Partial or invalid dynamic configuration also exits 2 before scanner logic.
This prevents a production-like manual invocation from reporting a false
natural PASS or duplicating the scanner under the observability contract.

## One-shot observer

The observer independently applies the same UTC schedule contract, opens one
derived JSON path, prints the existing fixed 23 redacted fields, and exits. A
future observer scheduled 15 minutes after a natural run uses this stable form:

```text
/usr/bin/python3 /opt/slimy/pm_updown_bot_bundle/scripts/expanded_shadow_run_status_redacted.py --status-root /opt/slimy/pm_updown_bot_bundle/runtime/expanded_shadow_run_status --slot-lookup-window-seconds 1800 --schedule-tolerance-seconds 900 --require-capture-mode spool
```

The 1800-second observer lookup window identifies the current even-hour slot;
the separate 900-second schedule tolerance still rejects a scanner STARTED
outside the accepted startup grace. The reader does not inspect logs, list
other records, inspect spool data, or poll. A late observer outside its lookup
window exits nonzero and must trigger fail-safe rollback, never PASS.

## Future activation runbook (do not execute in this phase)

1. Obtain fresh exact-bounded approval for installed cron mutation and any
   capture activation. Confirm the reviewed source commit, clean repository,
   disabled baseline fingerprint, empty owner-only spool, no runtime SQLite,
   and no capture fields in direct scanner, micro-live, phase-all, or weather.
2. Pre-create or validate the stable status root as owner `slimy`, mode 0700.
   Do not delete or inspect any existing record.
3. Change only the existing direct Expanded Shadow Scanner entry. Add the two
   stable dynamic fields above and the separately approved stable capture flag
   and spool path through `/usr/bin/env` before the existing timeout command.
   Do not add a run ID, expected timestamp, or per-run status directory.
4. Leave the scanner's absolute executable/script paths and all existing
   arguments unchanged. Leave micro-live, phase-all, weather, main trading,
   and offline ingest unchanged.
5. Add one date-gated, one-shot observer using the stable command above, one
   later independent fail-safe, and one hard sunset. Each control entry must be
   locked, idempotent, self-removing, and separately reviewed. No model waits,
   tails logs, or polls.
6. Before installation, validate syntax, exact field placement, one direct
   scanner lane, one capture-enabled lane, stable-root ownership/mode, dynamic
   derivation for synthetic 14:00/16:00/18:00 starts, observer one-shot lookup,
   and byte-exact rollback to the accepted disabled fingerprint.
7. Install only within the approval window. Do not manually run scanner,
   trading, weather, ingest, or external API checks. Let the natural schedule
   invoke the direct scanner.
8. The observer accepts only an exact attributable PASS with required spool
   mode and zero warning/drop counts. NOT_STARTED, RUNNING, WARN, FAIL, reader
   error, wrong run/schedule, stale record, duplicate/malformed state, runtime
   SQLite, isolation failure, or fingerprint mismatch invokes rollback.
9. The fail-safe rolls back unless the one-shot observer left reviewed PASS
   evidence. The hard sunset rolls back unless a fresh keep decision exists.
   Neither path deletes or reads status/spool payloads.

## Future exact rollback scope

Rollback removes only fields added to the direct scanner for the activation:

- `EXPANDED_SHADOW_DYNAMIC_ATTRIBUTION`;
- `EXPANDED_SHADOW_RUN_STATUS_ROOT`;
- `EXPANDED_SHADOW_SCHEDULE_GRACE_SECONDS`, only if explicitly installed;
- `CANDIDATE_LEDGER_SHADOW_ENABLED` and `CANDIDATE_LEDGER_SPOOL_PATH`, only if
  they were added by that activation.

It also removes only the activation's exact observer, fail-safe, and sunset
control entries by their reviewed identifiers. It must preserve all unrelated
cron bytes and restore the exact accepted disabled fingerprint. It must not
delete or truncate status records, claim markers, spool files, logs, or any
other runtime evidence. Micro-live, phase-all, weather, main trading, and
offline ingest remain untouched.

## Rejected alternatives

1. A tracked shell wrapper would add another executable boundary, argument and
   signal forwarding duties, shell quoting risk, and a second place to enforce
   duplicate handling. The existing Python CLI already owns observation setup,
   so the wrapper is larger without improving isolation.
2. A separate attribution command composed into cron would require command
   substitution or shell parsing to move derived values into the scanner and
   observer. That creates another quoting/failure boundary and does not make
   the claim atomic with scanner startup.
3. Keeping the explicit three-field contract for natural runs repeats the
   proven stale-binding failure and requires cron mutation every two hours.

The integrated optional mode is the smallest design that performs derivation
and exclusive claim immediately before the unchanged scanner call.
