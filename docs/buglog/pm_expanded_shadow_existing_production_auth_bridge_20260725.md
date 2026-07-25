# Existing production-auth bridge for redacted discovery

## Problem

The purpose-built redacted discovery diagnostic expected `KALSHI_KEY` plus an
in-memory `KALSHI_PRIVATE_KEY_PEM`. Tracked production source instead uses the
canonical `KALSHI_KEY` and `KALSHI_SECRET_FILE` path contract. The agent shell
also lacked the diagnostic fields, so the prior warning combined a
diagnostic key-source mismatch with a process-context gap; it did not prove
that production credentials were globally absent.

## Repair

- Kept the normal redacted CLI and `utils/kalshi_redacted_discovery.py`
  credential-blind.
- Added an authenticated-header injection seam to the existing diagnostic core
  while preserving the established value-based wrapper.
- Added an operator-only launcher that defaults to value-free no-network
  preflight and requires `--execute-once` for any later request.
- Reused `utils.kalshi_orders.KalshiOrderClient`, the same factory already used
  by the redacted health checker and live order paths.
- Passed only the authenticated client's signed-header callable into discovery;
  no credential value, key path, or field name enters the redacted core.
- Added a separate static gate for the intentionally privileged bridge.

## Safety

No production client was constructed during implementation. Validation used
only injected fake clients, fake factories, disposable synthetic paths, and
synthetic values. No live request, order, capture, cron mutation, credential
load, service restart, or production activation occurred.
