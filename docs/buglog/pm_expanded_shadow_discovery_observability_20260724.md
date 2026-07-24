# Expanded Shadow discovery failures could masquerade as zero markets

## Problem

The direct Expanded Shadow Scanner consumed the legacy list-only discovery
function. Missing configuration, request failures, authentication rejection,
non-success HTTP responses, malformed JSON, schema/parser failures, legitimate
empty responses, and filter-empty results could all become `[]`. Pagination
was not followed. The run status therefore could record a clean zero-market
completion without proving discovery succeeded.

The existing main-market inventory output also emitted record-derived ticker
samples, which was not suitable for the stricter one-shot diagnosis.

## Repair

- Added a separate structured diagnostic discovery function with a closed
  outcome taxonomy, bounded stage counts, complete cursor pagination, and a
  list compatibility adapter.
- Limited diagnostic adoption to the direct shadow CLI. Existing trading and
  other list-returning callers keep the legacy path.
- Added additive discovery fields to atomic run status and the one-shot status
  reader. Discovery failures now exit nonzero and write `FAILED`.
- Added a purpose-built redacted one-shot discovery command with twelve fixed
  fields and no record-derived output.
- Removed ticker samples from the existing main inventory formatter.
- Added synthetic coverage for success, configuration/auth/network/HTTP,
  JSON/schema/parser, filters, pagination, observer status, redaction,
  candidate/capture compatibility, and zero runtime SQLite access.

## Safety

Source, tests, and documentation only. No live discovery, scanner, trading,
weather, ingest, cron, database, service, timer, tmux, Caddy, DNS, or capture
activation was performed. The later one-shot live verification remains
separately approved work.
