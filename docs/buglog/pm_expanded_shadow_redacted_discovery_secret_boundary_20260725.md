# Redacted discovery credential-boundary repair

## Problem

The purpose-built redacted discovery CLI delegated to the established
diagnostic wrapper. When no key object was injected, that wrapper opened a
configured private-key file. The prior live-verification phase therefore
stopped at its static safety gate before any external request.

## Repair

- Preserved the established file-based wrapper for legacy scanner callers.
- Extracted an authenticated diagnostic core that never opens credential
  files.
- Routed only the redacted one-shot CLI through inherited in-memory
  authentication or an injected client.
- Added a value-free inherited-runtime preflight and a function-scoped AST
  dependency-closure checker.
- Made missing or malformed inherited authentication return the existing
  bounded `AUTH_CONFIGURATION_MISSING` outcome before any request.
- Kept the reviewed discovery taxonomy, pagination, stage counts, filters, and
  output allowlist unchanged.

## Safety

This is a source/test/docs repair only. No live discovery, production scanner,
trading, weather, offline ingest, cron mutation, database action, service
restart, or credential-file access is part of the repair.
