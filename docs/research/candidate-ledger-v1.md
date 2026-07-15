# Candidate ledger v1

Phase 1A is an offline research foundation. It does not capture production
candidates, import production runners, place orders, call external services, or
modify strategy parameters.

## Storage contract

The canonical candidate snapshot conforms to
`schemas/candidate_ledger/v1.schema.json` (`schema_version=1.0.0`). A candidate
is written once as `candidate_observed`; later facts are additional typed events.
SQLite triggers reject `UPDATE` and `DELETE` on the event table. The API exposes
no mutation method, transactions use `BEGIN IMMEDIATE`, and the supported
deployment contract is one writer per database.

Every CLI requires an explicit local database path. There is no default path,
network client, production hook, or environment-based database discovery.

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

Phase 1B—production candidate capture or any production import—requires a new
operator-approved phase. Autonomous production self-modification remains
permanently prohibited.
