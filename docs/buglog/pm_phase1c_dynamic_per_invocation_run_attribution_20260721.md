# Phase 1C dynamic per-invocation run attribution

Date: 2026-07-21

## Failure re-derived

The accepted atomic STARTED / COMPLETED / FAILED record required three explicit
values from the installed direct scanner entry: run ID, expected scheduled UTC
timestamp, and status directory. The first natural run at 14:00 UTC completed
successfully, but the unchanged entry still named that completed run and its
directory for every later two-hour invocation. A static cron binding therefore
cannot represent more than one natural run and would either overwrite, reuse,
or be rejected against stale attribution.

## Repair

- Add a disabled-by-default dynamic mode to the existing run-observation setup.
- Derive the preceding even-hour UTC slot from observation initialization time
  and accept it only within an inclusive 900-second startup grace.
- Derive canonical `expanded-shadow-YYYYMMDDTHHMMSSZ` IDs and one JSON path
  beneath a stable absolute status root.
- Claim each derived run ID with atomic exclusive file creation. Persistent
  claims block concurrent, completed, failed, stale-claim, and malformed-status
  duplicates before scanner logic, without overwriting evidence.
- Extend the one-shot redacted reader with a stable-root mode that independently
  derives the current natural slot and performs exactly one status lookup.
- Preserve the legacy explicit triple for disposable synthetic tests and keep
  dynamic attribution off by default.

## Behavior boundary

Only attribution setup, duplicate exclusion, and status observation differ.
The body and signature of `optimize_kalshi_strategy` are unchanged. No market
discovery, filtering, selected markets, priors, probabilities, gates, rejection
reasons, sizes, prices, order intent/attempt/submission, return contract,
weather path, main trading path, capture format, spool ingest, or runtime SQLite
behavior changed.

## Production posture

Source, tests, and documentation only. Installed cron and the disabled capture
baseline remain unchanged. No scanner, trading, weather, ingest, external API,
service, timer, tmux, Caddy, DNS, or runtime database action is part of this
repair. Independent review and fresh activation approval remain required.
