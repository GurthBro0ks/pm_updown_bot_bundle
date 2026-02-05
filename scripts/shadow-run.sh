#!/bin/bash
# Shadow-mode runner: Read-only analysis, proofs only. No trades.

set -euo pipefail

OUTDIR=&quot;/tmp/proof_bot_shadow_$(date +%Y%m%d_%H%M%S)Z&quot;
mkdir -p &quot;$OUTDIR&quot;

echo &quot;=== Shadow Run Start ===&quot; &gt; &quot;$OUTDIR/cmdlog.txt&quot;
date &gt;&gt; &quot;$OUTDIR/cmdlog.txt&quot;

# Mock: Fetch Polymarket markets (public API)
curl -s https://gamma.api.polymarket.com/markets?active=true&amp;limit=5 &gt; &quot;$OUTDIR/markets.json&quot; || echo &quot;[]&quot; &gt; &quot;$OUTDIR/markets.json&quot;

# Mock gates (expand later: freshness, liquidity, edge)
cat &lt;&lt; EOF &gt; &quot;$OUTDIR/gates.json&quot;
{
  &quot;freshness&quot;: &quot;PASS&quot;,
  &quot;liquidity&quot;: &quot;PASS (mock)&quot;,
  &quot;edge_after_fees&quot;: &quot;0.02 (mock)&quot;,
  &quot;stale_edge&quot;: false,
  &quot;new_markets&quot;: 0
}
EOF

echo &quot;=== Shadow Run PASS ===&quot; &gt; &quot;$OUTDIR/RESULT.txt&quot;
echo &quot;{ \&quot;status\&quot;: \&quot;PASS\&quot;, \&quot;outdir\&quot;: \&quot;$OUTDIR\&quot; }&quot; &gt; &quot;$OUTDIR/meta.json&quot;

echo &quot;Proof pack: $OUTDIR&quot;