# Candidate ledger accumulated read-path optimization

Date: 2026-07-16

## Problem

At 50,000 accumulated candidates and 176,262 events, the redacted status CLI
and the only validation mode both took about 36 seconds because status invoked
an exhaustive historical JSON/hash/schema scan. The grouped count and latest
candidate queries also lacked a matching index.

## Fix

- Added migration v2 with one evidence-backed index on
  `(event_type, event_timestamp DESC, sequence DESC)`.
- Kept full SQLite `integrity_check` in the default quick path while limiting
  payload/hash/schema/relationship checks to the 100 most recent events.
- Added explicit `--deep` offline validation for the exhaustive historical
  safety contract; the previous `CandidateLedger.validate()` API remains an
  exhaustive alias.
- Added schema metadata, required-index, migration, trigger, and integrity
  fields to redacted operational status.
- Cached candidate snapshots within one append transaction to remove redundant
  per-event lookups without changing durability or append-only enforcement.

## Result

At the same 50,000-candidate/176,262-event scale, status p95 improved from
35.820 seconds to 1.870 seconds and quick validation p95 is 1.534 seconds.
Deep validation remains exhaustive and completed with a 32.904-second p95.
Capture flush p95/p99 improved from 193.048/195.862 ms to
181.676/183.899 ms against the unchanged 250 ms timeout. Overall benchmark
classification is `PASS_BOUNDED`.

Capture remains disabled by default. No runtime, cron, trading, threshold,
category, max-days, weather, service, production database, or external API
behavior changed.
