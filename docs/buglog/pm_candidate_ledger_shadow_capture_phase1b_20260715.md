# Phase 1B disabled shadow candidate capture

## Scope

Added a one-way observational adapter from the existing main Kalshi evaluation
pipeline to the accepted Phase 1A append-only candidate ledger.

## Safety contract

- Capture defaults disabled and has no default database path.
- Enabling requires the exact `true` flag plus an explicit local SQLite path.
- Candidate observations are collected only after gate outcomes are final.
- Ledger data is never read by market selection, scoring, gates, sizing, or the
  order path.
- One bounded event batch is flushed near the existing proof/diagnostic stage.
- Configuration, malformed payload, lock, migration, and write failures are
  caught and reduced to redacted status counts.
- Capture failure does not change decisions, order intents, sizing, orders, or
  process exit status.
- No cron/runtime activation, network transmission, GreedBot integration,
  weather change, or autonomous self-modification was added.

## Validation status

Implementation tests cover disabled/no-path behavior, accepted and rejected
candidates, gate completeness, order intent, idempotency, batch bounds,
database lock/migration failures, malformed/secret-looking payload rejection,
database permissions, direct status CLI output, no-network behavior, and
disabled/enabled/failure behavior equivalence. Full operator QA remains pending.
