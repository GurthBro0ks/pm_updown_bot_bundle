# Expanded Shadow discovery observability

## Scope

The direct `strategies/kalshi_optimize.py --mode shadow` entrypoint uses the
structured diagnostic discovery path. Existing list-returning discovery and
all non-shadow trading callers retain their previous interface and behavior.
The diagnostic path is read-only and does not enable capture, databases,
orders, cron, services, or weather.

## Result contract

`KalshiDiscoveryResult` contains a closed `DiscoveryOutcome`, a compatibility
`market_list()` adapter, bounded stage counts, a bounded request-status class,
and a fixed endpoint label. It never contains response bodies, URLs, exception
messages, headers, credentials, account data, or record samples.

Success outcomes:

- `SUCCESS_NONEMPTY`
- `SUCCESS_EMPTY`

Failure outcomes:

- `AUTH_CONFIGURATION_MISSING`
- `AUTH_REJECTED`
- `NETWORK_TIMEOUT`
- `NETWORK_ERROR`
- `HTTP_ERROR`
- `JSON_PARSE_ERROR`
- `SCHEMA_ERROR`
- `PAGINATION_ERROR`
- `INTERNAL_DISCOVERY_ERROR`

Counts are nonnegative when a stage was reached. `-1` in the atomic status or
redacted output means unavailable because that stage was not reached. It never
means zero.

## Stage semantics

- `request_attempted`: `0` or `1`; header/configuration failure before a
  request remains `0`.
- `page_count`: successfully decoded and schema-validated pages.
- `raw_record_count`: market records received across successful market pages.
- `parsed_record_count`: raw market records accepted by the normalization
  boundary.
- `active_record_count`: parsed records with an active/open status.
- `price_liquidity_eligible_count`: active records that have a usable price and
  satisfy the unchanged configured discovery-liquidity threshold.
- `expiry_eligible_count`: diagnostic records remaining after the scanner's
  unchanged expiry policy.
- `category_eligible_count`: records remaining after the scanner's unchanged
  category policy.
- `final_eligible_count`: records passed to candidate creation.

`total_markets` is the final eligible count for successful runs. It is
unavailable in a discovery-failure status. `candidates_processed` remains the
AI/candidate-cascade attempt count, so eligible records with zero created
candidates stay distinct. Candidate-observed and gate-evaluated counts retain
their candidate-ledger definitions.

## Pagination and failure behavior

The diagnostic path follows the response cursor for both series and markets,
with a hard page bound and repeated-cursor rejection. A later-page failure
returns `PAGINATION_ERROR`; partial records are not returned as success.

`SUCCESS_EMPTY` exits zero and writes `COMPLETED`. Any diagnostic failure exits
two and writes `FAILED`; the exact-run observer therefore returns `FAIL`.
Unexpected exceptions continue to be recorded as `FAILED` and re-raised.
Legacy status records remain readable as `LEGACY_UNAVAILABLE` for the additive
discovery fields.

## One-shot redacted live verification runbook

This runbook is documentation only. Do not execute it without a separate,
fresh, exact operator approval for one read-only external discovery call.

1. Verify the repository commit and clean state.
2. Verify capture, dynamic attribution, micro-live capture, phase-all capture,
   and weather capture remain disabled; verify cron remains at the accepted
   disabled fingerprint.
3. Use an already provisioned process environment. Do not open, print, source,
   copy, or inspect any secret-bearing file.
4. Run exactly once:

   `python3 scripts/expanded_shadow_discovery_redacted.py`

5. Stop after the command returns. Do not retry, poll, tail logs, inspect raw
   responses, or run the production scanner.
6. Accept only the twelve fixed output fields documented by the script. No
   market identifiers or record-derived values are permitted.

Classification:

- `SUCCESS_EMPTY` with `RAW_RECORD_COUNT=0`: legitimate empty market response.
- `SUCCESS_EMPTY` with raw records and lower later-stage counts: the first
  stage reaching zero identifies parser/status/category/expiry/price-liquidity
  elimination.
- Any failure enum: the actual cause is the named configuration, auth,
  network, HTTP, JSON, schema, pagination, or internal class.
- `SUCCESS_NONEMPTY`: discovery is working; later zero candidates must be
  classified from final eligible, candidates processed, candidate-observed,
  and gate-evaluated counts.

Rollback is a no-op because the one-shot command performs no source, capture,
cron, database, service, or trading mutation.
