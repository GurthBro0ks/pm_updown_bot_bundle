#!/bin/bash
set -euo pipefail

# Truth gate: Shadow runner produces proof pack
./scripts/shadow-run.sh
LATEST=$(ls -t /tmp/proof_bot_shadow_* | head -1)
if [ -f &quot;$LATEST/RESULT.txt&quot; ] &amp;&amp; grep -q PASS &quot;$LATEST/RESULT.txt&quot;; then
  echo &quot;✅ Tests PASS&quot;
  exit 0
else
  echo &quot;❌ Tests FAIL&quot;
  exit 1
fi