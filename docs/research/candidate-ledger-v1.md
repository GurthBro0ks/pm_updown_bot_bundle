# Candidate ledger v1

Phase 1A is the offline research foundation. Phase 1B adds a narrow
observational hook to the existing main evaluation pipeline, but capture is
disabled by default and has no tracked database path. It never places orders,
calls external services, reads ledger data into strategy logic, or modifies
strategy parameters.

## Storage contract

The canonical candidate snapshot conforms to
`schemas/candidate_ledger/v1.schema.json` (`schema_version=1.0.0`). A candidate
is written once as `candidate_observed`; later facts are additional typed events.
SQLite triggers reject `UPDATE` and `DELETE` on the event table. The API exposes
no mutation method, transactions use `BEGIN IMMEDIATE`, and the supported
deployment contract is one writer per database.

Every SQLite CLI requires an explicit local database path. The Phase 1B runtime
adapter requires both `CANDIDATE_LEDGER_SHADOW_ENABLED=true` and an explicit,
existing local `CANDIDATE_LEDGER_SPOOL_PATH`. There is no default spool or
database path and no network client. Runtime capture never opens, migrates, or
writes SQLite. Invalid configuration produces only a redacted warning.

## Identity and idempotency

`candidate_id` is a SHA-256-derived identifier over canonical public/research
identity fields: run id/timestamp, strategy version, git commit, ticker, side,
and observed price. `event_id` is derived from candidate id, event type,
timestamp, and canonical event payload. Re-appending byte-equivalent canonical
content is a no-op; reusing an id for different content is rejected.

## Event types

The only v1 event types are:

- `candidate_observed`
- `gate_evaluated`
- `order_intent_created`
- `order_attempted`
- `order_result`
- `fill_observed`
- `settlement_observed`
- `hypothetical_outcome_computed`
- `experiment_assignment`
- `review_recorded`

Unknown event types fail validation. Candidate snapshots are immutable.
Settlement, fills, hypothetical outcomes, assignments, and reviews never
rewrite the original snapshot.

## Secret and privacy boundary

The schema uses closed objects and the runtime validator rejects secret-bearing
field names, private-key blocks, authorization/bearer material, credential
fields, webhook endpoints, raw prompt/completion fields, raw external response
bodies, and private account identifiers. `policy_version` is an identifier, not
prompt text. `internal_order_reference` is bounded internal correlation data,
not a broker credential or private account id.

## Replay rules

Binary payoff and scoring functions are deterministic. Prices are cents per
contract. Fees must be supplied explicitly; fee-dependent PnL is unknown when
fees are unknown. Maker and taker fee inputs remain separate until an explicit
maker/taker assumption selects one. No Kalshi fee constant is embedded. The
`ai_prior` field is the predicted probability of a YES settlement. Train, validation, and
holdout assignments use registered UTC cutoffs, never shuffle, reject duplicate
candidate ids, and reject source timestamps later than candidate observation.

## Disabled shadow capture and durable local spool

The runtime adapter collects validated public/non-secret candidate and gate
events in memory after decisions are final. It caps candidates per run and
publishes at most one immutable, checksummed, versioned batch into an explicit
existing spool directory. Publication uses an owner-only temporary file,
`fsync`, and atomic rename under a local writer lock. Deterministic batch names
make identical runtime retries idempotent; conflicting or truncated files fail
closed. Runtime errors are reduced to bounded warning/drop counts.

`CANDIDATE_LEDGER_CAPTURE_MAX_PER_RUN`,
`CANDIDATE_LEDGER_SPOOL_MAX_EVENTS_PER_BATCH`,
`CANDIDATE_LEDGER_SPOOL_MAX_BATCH_BYTES`,
`CANDIDATE_LEDGER_SPOOL_MAX_BYTES`, and
`CANDIDATE_LEDGER_SPOOL_MAX_BATCHES` are bounded controls. The defaults permit
100 candidates, 300 events, a 2 MiB batch, a 256 MiB spool, and 10,000 batches.
No runtime rotation or deletion occurs: capacity exhaustion produces a warning
and preserves every prior batch.

`scripts/candidate_capture_spool_ingest.py` is the only spool-to-SQLite path.
It requires explicit `--spool` and `--database` paths, verifies framing,
checksums, version, closed payload schemas, and deterministic batch identity,
then commits one batch and its append-only ingestion marker in the same SQLite
transaction. Identical ingest retries are no-ops; conflicts roll back; corrupt
batches cannot damage ledger history; spool files are retained on both success
and failure. `--dry-run` validates without creating or changing a database.

No ledger query is present on the selection, scoring, gate, sizing, or order
path. Capture failure cannot change a decision, block an order, or change a
successful process exit status. Status tools expose counts and health only,
never raw candidates. Installed cron does not set any capture or spool field.
Production activation requires a new exact-bounded approval. GreedBot
integration and autonomous production self-modification remain prohibited.
