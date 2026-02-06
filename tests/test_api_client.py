"""Tests for bot.api_client — stub backend and unified client."""

import unittest

from bot.api_client import ApiClient, StubBackend
from bot.config import BotConfig


class TestStubBackend(unittest.TestCase):
    """StubBackend should simulate order flow without real HTTP."""

    def setUp(self):
        self.backend = StubBackend()

    def test_place_order_returns_filled(self):
        order = self.backend.place_order("market-1", "BUY", 0.60, 0.50)
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(order.market_id, "market-1")
        self.assertEqual(order.side, "BUY")
        self.assertEqual(order.price, 0.60)
        self.assertEqual(order.size_usdc, 0.50)
        self.assertIsNotNone(order.order_id)

    def test_place_order_tracked_in_positions(self):
        self.backend.place_order("market-2", "SELL", 0.40, 0.25)
        positions = self.backend.get_positions()
        self.assertIn("market-2", positions)
        self.assertEqual(positions["market-2"].side, "SELL")

    def test_cancel_order_marks_cancelled(self):
        order = self.backend.place_order("m", "BUY", 0.50, 0.10)
        # Manually set to OPEN for cancellation test
        order.status = "OPEN"
        result = self.backend.cancel_order(order.order_id)
        self.assertTrue(result)
        self.assertEqual(order.status, "CANCELLED")

    def test_cancel_nonexistent_returns_false(self):
        self.assertFalse(self.backend.cancel_order("nope"))

    def test_cancel_all(self):
        o1 = self.backend.place_order("m1", "BUY", 0.50, 0.10)
        o2 = self.backend.place_order("m2", "BUY", 0.50, 0.10)
        o1.status = "OPEN"
        o2.status = "OPEN"
        cancelled = self.backend.cancel_all()
        self.assertEqual(cancelled, 2)

    def test_get_open_orders_filters_correctly(self):
        o1 = self.backend.place_order("m1", "BUY", 0.50, 0.10)
        o2 = self.backend.place_order("m2", "BUY", 0.50, 0.10)
        o1.status = "OPEN"
        # o2 stays FILLED
        open_orders = self.backend.get_open_orders()
        self.assertEqual(len(open_orders), 1)
        self.assertEqual(open_orders[0].order_id, o1.order_id)

    def test_get_market_price_deterministic(self):
        price = self.backend.get_market_price("any-market")
        self.assertEqual(price, 0.50)

    def test_call_log_records_operations(self):
        self.backend.place_order("m", "BUY", 0.50, 0.10)
        self.backend.get_market_price("m")
        self.assertGreaterEqual(len(self.backend.call_log), 2)
        methods = [c["method"] for c in self.backend.call_log]
        self.assertIn("place_order", methods)
        self.assertIn("get_market_price", methods)


class TestApiClient(unittest.TestCase):
    """ApiClient wraps backend and respects config."""

    def test_dry_run_uses_stub(self):
        cfg = BotConfig(dry_run=True)
        client = ApiClient(cfg)
        self.assertIsInstance(client._backend, StubBackend)

    def test_live_mode_still_uses_stub_for_now(self):
        """Until real HTTP layer is approved, even live mode uses stubs."""
        cfg = BotConfig.micro_live()
        client = ApiClient(cfg)
        self.assertIsInstance(client._backend, StubBackend)

    def test_place_order_delegates(self):
        cfg = BotConfig(dry_run=True)
        client = ApiClient(cfg)
        order = client.place_order("m", "BUY", 0.50, 0.25)
        self.assertEqual(order.market_id, "m")

    def test_call_log_accessible(self):
        cfg = BotConfig(dry_run=True)
        client = ApiClient(cfg)
        client.place_order("m", "BUY", 0.50, 0.25)
        self.assertGreater(len(client.call_log), 0)


if __name__ == "__main__":
    unittest.main()
