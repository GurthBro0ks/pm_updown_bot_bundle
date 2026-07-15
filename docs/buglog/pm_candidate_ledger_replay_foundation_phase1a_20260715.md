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
require separate approval — not authorized by this closeout.

## Closeout (2026-07-15)

Independent Claude safety/architecture review passed, independently re-running
every validation command with matching results (proof:
`/tmp/proof_pm_candidate_ledger_replay_phase1a_independent_review_20260715T161040Z`).
Operator manual QA of the schema/event vocabulary and bounded CLI output passed
and accepted commit `9c37bdf516ffc82fcd55c41ff3fa081f4208bfdc`. Both proofs were
scanned for secrets (zero findings) and persisted byte-for-byte under
`proofs/pm_updown_bot_bundle/`. `passes`/`accepted` are now `true` in
`feature_list.json`. Production capture remains disabled and Phase 1B remains
unauthorized.
