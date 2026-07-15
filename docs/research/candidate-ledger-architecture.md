# Candidate ledger architecture decisions

1. **Event store, not mutable rows.** The first event owns the immutable v1
   snapshot. New evidence is another event.
2. **Standard library only.** SQLite, JSON, hashing, datetime, and CLI handling
   use Python standard-library modules. There is no network dependency.
3. **Single writer.** `BEGIN IMMEDIATE` makes the bounded writer contract
   explicit. Read-only tools open SQLite with `mode=ro`.
4. **Fees are inputs.** Missing maker/taker fees remain unknown; the replay
   layer never invents exchange constants.
5. **Chronology is an invariant.** UTC cutoffs are ordered and assignments are
   derived from candidate timestamps. Source data newer than the candidate is
   rejected as leakage.
6. **Research cannot deploy.** The package has no production runner imports,
   code-writing path, cron integration, strategy mutation, or external API.
7. **Bounded summaries.** CLIs emit counts and aggregate metrics, never raw
   candidate payloads by default.
