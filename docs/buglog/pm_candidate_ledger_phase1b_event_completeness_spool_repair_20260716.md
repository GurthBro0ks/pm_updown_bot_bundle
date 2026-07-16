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
