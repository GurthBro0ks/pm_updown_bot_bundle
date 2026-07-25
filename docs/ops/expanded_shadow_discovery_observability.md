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

The normal `expanded_shadow_discovery_redacted.py` CLI is credential-blind. It
does not read environment variables, load dotenv, open files, parse keys,
source shell files, accept credential arguments or stdin, or construct a
production client. Without an injected client it fails closed before a
request.

The separate operator-only
`expanded_shadow_discovery_production_auth.py` launcher reuses the established
`KalshiOrderClient` construction boundary. That privileged constructor resolves
the canonical production field names `KALSHI_KEY` and `KALSHI_SECRET_FILE`;
the latter is a path source. The resulting authenticated client supplies only
its signed-header callable to the credential-blind discovery core. No second
private-key environment value or credential store is required.

1. The operator confirms the existing production authentication configuration
   is installed. The operator does not paste, export, duplicate, or edit
   credentials for this workflow.
2. Confirm the repository commit, branch, origin equality, and clean state.
3. Confirm production capture, dynamic attribution, micro-live capture,
   phase-all capture, weather capture, and offline ingest remain disabled;
   confirm cron remains at the accepted disabled fingerprint.
4. Run only the two static gates:

   `python3 scripts/check_expanded_shadow_discovery_secret_boundary.py`

   `python3 scripts/check_expanded_shadow_production_auth_bridge.py`

   The first gate covers only the credential-blind redacted closure. The
   second separately verifies the privileged launcher uses the reviewed
   client factory, has no credential CLI/stdin path, has no order,
   capture, cron, retry, or polling call, and is not mislabeled secret-blind.
   Passing static results do not replace source review.

5. The operator runs the launcher with no arguments. This is its default
   no-network preflight:

   `python3 scripts/expanded_shadow_discovery_production_auth.py`

   It may report only:

   - `PRODUCTION_AUTH_CONTRACT`
   - `AUTH_CLIENT_FACTORY_AVAILABLE`
   - `REQUIRED_AUTH_FIELD_NAMES`
   - `REQUIRED_AUTH_FIELD_COUNT`
   - `AUTH_CONFIGURATION_PRESENT`
   - `PRIVATE_KEY_SOURCE_TYPE`
   - `PRIVATE_KEY_FILE_EXISTS`
   - `PRIVATE_KEY_FILE_PERMISSION_STATUS`
   - `AUTH_PARSE_STATUS`
   - `DIRECT_SECRET_VALUE_OUTPUT`
   - `NETWORK_CALL_PERFORMED`

   It emits no values, path, identifier, hash, exception, or traceback. The
   default preflight checks only field presence and path metadata; parse status
   remains `not_run` unless a separately reviewed synthetic checker is
   injected.
6. Require `PRODUCTION_AUTH_CONTRACT=PASS`,
   `AUTH_CLIENT_FACTORY_AVAILABLE=yes`,
   `AUTH_CONFIGURATION_PRESENT=yes`, and
   `NETWORK_CALL_PERFORMED=no`. Otherwise stop without a request and report:

   `RESULT=WARN`

   `NEXT_STEP=operator_manual_secret_action_required`

7. Obtain fresh live-chat approval containing exactly:

   - `APPROVAL_SOURCE=live_chat_turn`
   - `APPROVED_ACTION=<exact bounded label>`
   - a fresh nonce
   - issued and expiry timestamps
   - `APPROVAL_DENIES`
   - `APPROVAL_STATEMENT`

   Do not persist the raw nonce in proof, reports, progress, or notifications.
8. During the approval window, the operator or separately approved bounded
   executor invokes exactly one discovery operation:

   `python3 scripts/expanded_shadow_discovery_production_auth.py --execute-once 2>/dev/null`

   The explicit action flag is mandatory. The launcher constructs the
   production client once, invokes discovery once, permits zero retries, and
   enforces a hard 60-second wall-clock timeout. It has no polling loop.
9. Do not retry, poll, sleep, tail logs, or run the production scanner.
10. Discard stderr without inspection and do not persist it.
11. Validate stdout against the exact twelve-field allowlist. Reject unknown
    keys, malformed lines, raw response data, URLs, identifiers, record-derived
    values, or exception text.
12. Reconfirm production remains disabled and cron remains at the accepted
    fingerprint.

Credential values must never be pasted into an agent prompt, passed through CLI
arguments or stdin, duplicated into a new environment variable, or recorded in
shell history. This workflow does not instruct the operator to edit an
environment file or key file. If the operator context lacks the established
configuration, only the operator may repair that existing production boundary.

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
