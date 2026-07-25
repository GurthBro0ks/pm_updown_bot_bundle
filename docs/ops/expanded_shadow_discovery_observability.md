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

The redacted command's credential source is
`inherited_process_environment_or_injected_client_only`. Its tracked CLI uses
only the already-inherited process environment. It does not load dotenv, open
key material, source shell files, accept credential arguments or stdin, or
delegate to a secret-loading wrapper. Credential provisioning is controlled by
the operator, outside the agent command.

1. Confirm the repository commit, branch, origin equality, and clean state.
2. Confirm production capture, dynamic attribution, micro-live capture,
   phase-all capture, weather capture, and offline ingest remain disabled;
   confirm cron remains at the accepted disabled fingerprint.
3. Run the static dependency-closure gate:

   `python3 scripts/check_expanded_shadow_discovery_secret_boundary.py`

   The gate resolves the reviewed constant-string `getattr`, import-alias,
   simple-assignment, chained target, environment, file, subprocess, and
   output indirection classes. It fails closed on unresolved dynamic
   `getattr` for security-relevant or non-demonstrably-safe bases. Its scope
   is this redacted command and the exact reviewed dependency closure; it does
   not prove arbitrary Python metaprogramming universally safe. A passing
   result remains one gate and does not replace source or independent review.

4. Run the inherited-runtime-context preflight:

   `python3 scripts/expanded_shadow_discovery_auth_preflight_redacted.py`

   It may report only:

   - `AUTH_RUNTIME_CONTEXT`
   - `REQUIRED_AUTH_FIELD_NAMES`
   - `MISSING_AUTH_FIELD_COUNT`
   - `AUTH_PARSE_STATUS`
   - `DIRECT_SECRET_FILE_ACCESS`
   - `NETWORK_CALL_PERFORMED`

5. If the preflight is not `PASS`, stop without a request and report:

   `RESULT=WARN`

   `NEXT_STEP=operator_manual_secret_action_required`

   The agent must not read or load an environment file, key file, or other
   credential source to repair the shell context.
6. Only after a fresh exact-bounded approval, run exactly once:

   `python3 scripts/expanded_shadow_discovery_redacted.py`

7. Do not retry.
8. Do not poll, sleep, tail logs, or run the production scanner.
9. Discard stderr without inspection and do not persist it.
10. Validate stdout against the exact twelve-field allowlist. Reject unknown
    keys, malformed lines, raw response data, URLs, identifiers, record-derived
    values, or exception text.
11. Reconfirm production remains disabled and cron remains at the accepted
    fingerprint.

Credential values must never be pasted into an agent prompt, passed through CLI
arguments or stdin, or recorded in shell history. If the current shell lacks
the required inherited fields, only the operator may provision a new secure
runtime context. The future live command executes once with zero retries and
bounded redacted output.

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
