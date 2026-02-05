# pm_updown_bot_bundle

Polymarket trading bot (Python + Ralph proofs).

## Shadow Mode (Read-Only)

\`\`\`bash
./scripts/shadow-run.sh  # Produces /tmp/proof_bot_shadow_*/ (gates, markets.json)
\`\`\`

## Tests (Truth Gate)

\`\`\`bash
./scripts/run_tests.sh  # FAIL-CLOSED
\`\`\`

Success = structured proofs w/ PASS gates (freshness/liquidity/edge).
Live trades: After micro-live proof-gates.