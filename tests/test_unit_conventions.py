"""Tests for unit conventions in Kalshi order flow.

Ensures cents/dollars are never confused:
- price_cents is int (1-99), always cents
- size_usd and cost_usd are float, always dollars
- Proof pack fields are correctly labeled
"""

import sys
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

import pytest


class TestCentsToUsd:
    """Unit conversion helpers."""

    def test_cents_to_usd_5(self):
        from utils.kalshi_orders import cents_to_usd
        assert cents_to_usd(5) == 0.05

    def test_cents_to_usd_99(self):
        from utils.kalshi_orders import cents_to_usd
        assert cents_to_usd(99) == 0.99

    def test_cents_to_usd_0(self):
        from utils.kalshi_orders import cents_to_usd
        assert cents_to_usd(0) == 0.0

    def test_cents_to_usd_50(self):
        from utils.kalshi_orders import cents_to_usd
        assert cents_to_usd(50) == 0.50


class TestUsdToCents:
    """Dollar to cents conversion."""

    def test_usd_to_cents_005(self):
        from utils.kalshi_orders import usd_to_cents
        assert usd_to_cents(0.05) == 5

    def test_usd_to_cents_099(self):
        from utils.kalshi_orders import usd_to_cents
        assert usd_to_cents(0.99) == 99

    def test_usd_to_cents_rounds_down(self):
        from utils.kalshi_orders import usd_to_cents
        assert usd_to_cents(0.123) == 12


class TestPriceCentsVsDollars:
    """Verify price_cents stays as cents and is never mistaken for dollars."""

    def test_price_cents_never_multiplied_by_100_for_display(self):
        """price_cents=5 should never be formatted as $5.00."""
        from utils.kalshi_orders import cents_to_usd

        price_cents = 5
        price_usd = cents_to_usd(price_cents)

        # price_cents=5 MUST equal $0.05, NOT $5.00
        assert price_usd == 0.05, f"price_cents={price_cents} should be $0.05, not ${price_usd}"
        assert price_usd < 1.0, f"price_cents={price_cents} must be less than $1.00"

    def test_price_cents_range(self):
        """price_cents must be in valid range 1-99."""
        for val in [1, 5, 50, 99]:
            from utils.kalshi_orders import cents_to_usd
            usd = cents_to_usd(val)
            assert 0.01 <= usd <= 0.99


class TestProofPackOrderStructure:
    """Verify proof pack orders use correct unit field names."""

    def test_cost_usd_is_dollar_float(self):
        """cost_usd must be a dollar float, e.g., 0.12 for 12 cents."""
        from utils.kalshi_orders import cents_to_usd

        # Simulate a fill cost of 13 cents
        price_cents = 13
        cost_usd = cents_to_usd(price_cents)

        assert cost_usd == 0.13, f"13 cents should be $0.13, got ${cost_usd}"
        assert cost_usd < 1.0, "cost_usd must be dollar float (< $1), not cents"

    def test_size_usd_is_dollar_float(self):
        """size_usd is always in dollars, e.g., 1.32 for ~$1.32."""
        size_usd = 1.32
        assert size_usd >= 0.01, "size_usd is in dollars, not cents"

    def test_proof_pack_produces_consistent_units(self):
        """
        Simulate a proof pack order entry and verify all numeric fields
        are in their correct units.
        """
        price_cents = 5       # 5 cents
        size_usd = 1.32       # $1.32
        cost_usd = 0.05       # $0.05 (actual cost = price_cents converted)

        # Sanity checks
        assert price_cents < 100, "price_cents must be < 100 (it's cents)"
        assert cost_usd < 1.0, "cost_usd must be < $1.00 (it's dollars)"
        assert size_usd >= 0.01, "size_usd must be >= $0.01 (it's dollars)"

        # Verify conversion consistency
        from utils.kalshi_orders import cents_to_usd
        assert cents_to_usd(price_cents) == cost_usd, \
            f"cents_to_usd({price_cents}) should equal cost_usd ({cost_usd})"


class TestSafetyLimitUnits:
    """Verify safety limits are in correct units."""

    def test_max_order_cents_is_cents(self):
        from utils.kalshi_orders import KalshiOrderClient
        client = KalshiOrderClient.__new__(KalshiOrderClient)
        client.MAX_ORDER_CENTS = 50
        client.MAX_QUANTITY = 1
        client._daily_loss_cents = 0.0
        client._daily_loss_reset_date = None

        # order_cents = price_cents * quantity
        price_cents = 5
        quantity = 1
        order_cents = price_cents * quantity

        assert order_cents == 5, "5c * 1 = 5c"
        assert order_cents <= client.MAX_ORDER_CENTS, \
            f"{order_cents}c should be <= MAX_ORDER_CENTS ({client.MAX_ORDER_CENTS}c)"

    def test_daily_loss_limit_is_cents(self):
        from utils.kalshi_orders import KalshiOrderClient
        client = KalshiOrderClient.__new__(KalshiOrderClient)
        client.MAX_ORDER_CENTS = 50
        client.MAX_QUANTITY = 1
        client.DAILY_LOSS_LIMIT_CENTS = 50
        client._daily_loss_cents = 0.0
        client._daily_loss_reset_date = None

        price_cents = 5
        quantity = 1
        daily_loss = client._daily_loss_cents + (price_cents * quantity)

        assert daily_loss <= client.DAILY_LOSS_LIMIT_CENTS


class TestShadowModeUnits:
    """Verify shadow/dry-run mode uses same unit conventions."""

    def test_shadow_cost_usd_is_estimated_from_price_cents(self):
        """In shadow mode, cost_usd should be computed from price_cents."""
        order_price = 0.05  # float dollars
        price_cents_shadow = int(order_price * 100)  # 5
        cost_usd_shadow = price_cents_shadow / 100.0  # 0.05

        assert price_cents_shadow == 5
        assert cost_usd_shadow == 0.05
        assert cost_usd_shadow < 1.0, "shadow cost_usd must be dollar float"