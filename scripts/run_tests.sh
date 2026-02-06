#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "=== run_tests.sh ==="
echo "[INFO] $(date -Is)"
echo "[INFO] Python: $(python3 --version 2>&1)"

# ── Run unit tests ────────────────────────────────────────────────────────
echo ""
echo "--- Unit Tests ---"
python3 -m pytest tests/ -v --tb=short 2>&1
PYTEST_RC=$?

if [[ "$PYTEST_RC" -ne 0 ]]; then
  echo "[FAIL] Unit tests failed (exit $PYTEST_RC)"
  exit 1
fi

# ── Verify safety invariants (belt-and-suspenders) ────────────────────────
echo ""
echo "--- Safety Invariant Checks ---"

python3 -c "
from bot.config import ABSOLUTE_MAX_POSITION_USDC, ABSOLUTE_MAX_DAILY_LOSS_USDC, ABSOLUTE_MAX_OPEN_ORDERS
assert ABSOLUTE_MAX_POSITION_USDC == 1.00, f'MAX_POSITION must be 1.00, got {ABSOLUTE_MAX_POSITION_USDC}'
assert ABSOLUTE_MAX_DAILY_LOSS_USDC == 1.00, f'MAX_DAILY_LOSS must be 1.00, got {ABSOLUTE_MAX_DAILY_LOSS_USDC}'
assert ABSOLUTE_MAX_OPEN_ORDERS == 3, f'MAX_OPEN_ORDERS must be 3, got {ABSOLUTE_MAX_OPEN_ORDERS}'

from bot.config import BotConfig
cfg = BotConfig(max_position_usdc=9999)
assert cfg.max_position_usdc <= 1.00, f'Clamping failed: {cfg.max_position_usdc}'
print('[PASS] Hard caps enforced correctly')

cfg2 = BotConfig.micro_live()
assert cfg2.dry_run is False, 'micro_live should set dry_run=False'
assert cfg2.max_position_usdc == 1.00
print('[PASS] micro_live() factory correct')
"

echo ""
echo "--- Stub API Smoke Check ---"

python3 -c "
from bot.api_client import ApiClient
from bot.config import BotConfig

cfg = BotConfig(dry_run=True)
client = ApiClient(cfg)
order = client.place_order('test-market', 'BUY', 0.50, 0.25)
assert order.status == 'FILLED', f'Expected FILLED, got {order.status}'
assert len(client.call_log) > 0, 'Call log should record operations'
print('[PASS] Stub API smoke check')
"

echo ""
echo "=== ALL CHECKS PASSED ==="
exit 0
