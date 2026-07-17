# Phase 1B event completeness and runtime headroom repair

## Defects

The `prior_validation_failed` rejection recorded its shadow candidate only
inside the optional nearest-miss helper guard. With that helper unavailable,
an enabled capture run silently omitted both required evaluation events.

Runtime capture also opened and appended the accumulated SQLite ledger. Its
tail latency was non-stationary on the shared host and sometimes exceeded the
configured 250 ms SQLite timeout, so activation readiness depended on unstable
database timing.

## Repair

The rejection record call is now outside the helper guard. The two diagnostic
edge helpers remain guarded; a capture-only raw-edge fallback provides a valid
observation when the optional helper is absent. The decision and `continue`
control flow are unchanged.

Runtime persistence now writes one bounded, immutable, checksummed batch to an
explicit existing local spool directory. SQLite access moved to the explicit
offline ingest CLI. Deterministic batch and event IDs make runtime and ingest
retries idempotent. Atomic ingest markers, append-only triggers, corruption
checks, capacity limits, and failure-isolation tests preserve ledger history.

## Safety

Capture remains disabled by default. No installed cron or runtime path is
activated, and no trading, threshold, category, sizing, order, weather,
network, GreedBot, or self-modification behavior is changed.

## Closeout (2026-07-17)

Independent Claude safety/architecture review passed, independently re-running
every validation command with matching results (proof:
`/tmp/proof_pm_candidate_ledger_shadow_capture_phase1b_spool_repair_independent_review_20260716T175307Z`).
Live operator manual QA independently re-reproduced fresh evidence across all
9 mandated task areas (event completeness, helper-guard behavior, runtime
spool, zero-SQLite runtime path, offline ingest, append-only enforcement,
failure isolation, benchmark, production non-activation, trading/weather
invariants) and accepted commit `70d6a7213208dd3960a91c96c6fd2bbd626c8fd2`
(proof: `/tmp/proof_pm_candidate_ledger_shadow_capture_phase1b_operator_qa_20260717T090845Z`).
All three proofs were scanned for secrets (zero actual findings) and persisted
byte-for-byte under `proofs/pm_updown_bot_bundle/`. `passes`/`accepted` are now
`true` in `feature_list.json`. Production capture remains disabled and
activation remains separately unauthorized.
