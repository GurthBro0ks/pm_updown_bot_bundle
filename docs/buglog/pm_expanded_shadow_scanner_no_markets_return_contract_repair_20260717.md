# Expanded Shadow Scanner no-markets return-contract repair

Date: 2026-07-17

## Symptom

The direct Expanded Shadow Scanner reached a valid no-markets condition, emitted
its candidate-capture summary, and then raised `TypeError` before logging its
normal exit marker. The strategy's no-markets branch returned scalar `0`, while
the direct CLI and all other supported callers expected the successful path's
three-value return contract.

## Root cause

`optimize_kalshi_strategy()` had two normal return paths. The populated-market
path returned `(exit_code, candidates_processed, total_markets)`, but the
no-markets path returned only `0`. The caller correctly unpacked the established
three-value contract; the early return was the inconsistent branch.

## Repair

The no-markets branch now returns `(0, 0, 0)`. A matching return annotation and
docstring make the existing contract explicit. No caller coercion or catch-all
exception handling was added, so future shape violations remain visible.

## Regression coverage

- Exact tuple type, arity, field order, and empty values.
- Direct CLI no-markets exit status and normal exit marker.
- Capture disabled: no spool path or batch required.
- Capture enabled with a disposable local spool: PASS, zero warnings/drops,
  zero events/batches, and poisoned SQLite access remains untouched.
- AST enforcement that every normal strategy return is a three-item tuple.
- Existing non-empty capture and dry-run behavior suites remain authoritative
  for populated-market behavior equivalence.

## Production posture

This is source/test/docs/state only. Production capture remains disabled,
installed cron remains restored to its pre-activation fingerprint, the empty
production spool parent remains preserved, and no live runner or external API
was invoked for validation.
