# Phase 1A candidate ledger and replay foundation

## Scope

Implemented a standalone offline research foundation: closed v1 candidate
schema, append-only SQLite events, chronological split/leakage guards,
deterministic binary replay calculations, and explicit-path local CLIs.

## Safety properties

- No production runner imports the ledger, and the ledger imports no production
  runner, strategy, venue, or network client.
- There is no default database path or production candidate capture.
- SQLite triggers reject event updates and deletes; later outcomes are events.
- Missing fees remain unknown. No exchange fee constant was invented.
- Source timestamps newer than candidate observation fail closed.
- Knowledge-base acceptance requires prior settlement plus outer-loop
  provenance.
- No GreedBot integration or self-modifying strategy path exists.

## Validation

- Candidate-ledger focused suite: 42 passed.
- Full repository pytest suite: 609 passed, 1 pre-existing dependency warning.
- Project truth gate: `STATUS: ALL GATES PASS`.
- Schema JSON and all new Python modules/CLIs validate/compile.
- Direct CLI initialization, read-only validation/summary/replay, and repeated
  deterministic replay output passed against a synthetic empty local database.

## Deferred

Production candidate capture and any production runner wiring are Phase 1B and
require separate approval. Manual operator QA and independent safety review are
still required before this foundation is accepted.
