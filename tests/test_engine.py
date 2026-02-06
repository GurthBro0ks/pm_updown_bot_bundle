"""Tests for bot.engine — safety gates and engine orchestration."""

import os
import tempfile
import unittest

from bot.api_client import ApiClient
from bot.config import BotConfig
from bot.engine import Engine, GateVerdict, check_gates


class TestSafetyGates(unittest.TestCase):
    """Pre-trade gate chain must reject unsafe orders."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ks_path = os.path.join(self.tmpdir, "KILL_SWITCH")
        self.cfg = BotConfig(
            dry_run=True,
            kill_switch_path=self.ks_path,
            max_position_usdc=1.00,
            max_daily_loss_usdc=1.00,
            max_open_orders=3,
        )
        self.client = ApiClient(self.cfg)

    def tearDown(self):
        if os.path.exists(self.ks_path):
            os.unlink(self.ks_path)
        os.rmdir(self.tmpdir)

    def test_valid_order_passes(self):
        result = check_gates(self.cfg, self.client, size_usdc=0.50, price=0.50)
        self.assertEqual(result.verdict, GateVerdict.PASS)

    def test_kill_switch_rejects(self):
        self.cfg.activate_kill_switch("test")
        result = check_gates(self.cfg, self.client, size_usdc=0.50, price=0.50)
        self.assertEqual(result.verdict, GateVerdict.REJECT_KILL_SWITCH)

    def test_zero_size_rejected(self):
        result = check_gates(self.cfg, self.client, size_usdc=0, price=0.50)
        self.assertEqual(result.verdict, GateVerdict.REJECT_INVALID_PARAMS)

    def test_negative_size_rejected(self):
        result = check_gates(self.cfg, self.client, size_usdc=-1, price=0.50)
        self.assertEqual(result.verdict, GateVerdict.REJECT_INVALID_PARAMS)

    def test_price_zero_rejected(self):
        result = check_gates(self.cfg, self.client, size_usdc=0.50, price=0)
        self.assertEqual(result.verdict, GateVerdict.REJECT_INVALID_PARAMS)

    def test_price_at_one_rejected(self):
        result = check_gates(self.cfg, self.client, size_usdc=0.50, price=1.0)
        self.assertEqual(result.verdict, GateVerdict.REJECT_INVALID_PARAMS)

    def test_price_above_one_rejected(self):
        result = check_gates(self.cfg, self.client, size_usdc=0.50, price=1.5)
        self.assertEqual(result.verdict, GateVerdict.REJECT_INVALID_PARAMS)

    def test_oversized_position_rejected(self):
        result = check_gates(self.cfg, self.client, size_usdc=1.50, price=0.50)
        self.assertEqual(result.verdict, GateVerdict.REJECT_POSITION_LIMIT)

    def test_daily_loss_limit_rejects(self):
        self.cfg.record_pnl(-1.00)
        # Deactivate kill-switch so we can test the PnL gate specifically
        self.cfg.deactivate_kill_switch()
        result = check_gates(self.cfg, self.client, size_usdc=0.50, price=0.50)
        self.assertEqual(result.verdict, GateVerdict.REJECT_DAILY_LOSS)

    def test_open_order_limit_rejects(self):
        # Fill up open orders to the limit
        for i in range(3):
            o = self.client.place_order(f"m{i}", "BUY", 0.50, 0.10)
            o.status = "OPEN"  # Force to OPEN for test
        result = check_gates(self.cfg, self.client, size_usdc=0.10, price=0.50)
        self.assertEqual(result.verdict, GateVerdict.REJECT_OPEN_ORDER_LIMIT)


class TestEngine(unittest.TestCase):
    """Engine integrates gates + API client."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ks_path = os.path.join(self.tmpdir, "KILL_SWITCH")
        self.cfg = BotConfig(
            dry_run=True,
            kill_switch_path=self.ks_path,
        )
        self.engine = Engine(self.cfg)

    def tearDown(self):
        if os.path.exists(self.ks_path):
            os.unlink(self.ks_path)
        os.rmdir(self.tmpdir)

    def test_successful_order(self):
        attempt = self.engine.try_place_order("m1", "BUY", 0.60, 0.50)
        self.assertEqual(attempt.gate_result.verdict, GateVerdict.PASS)
        self.assertIsNotNone(attempt.order)
        self.assertEqual(attempt.order.status, "FILLED")

    def test_rejected_order_no_order_object(self):
        self.cfg.activate_kill_switch("test")
        attempt = self.engine.try_place_order("m1", "BUY", 0.60, 0.50)
        self.assertEqual(attempt.gate_result.verdict,
                         GateVerdict.REJECT_KILL_SWITCH)
        self.assertIsNone(attempt.order)

    def test_history_tracks_all_attempts(self):
        self.engine.try_place_order("m1", "BUY", 0.60, 0.50)
        self.engine.try_place_order("m2", "SELL", 0.40, 0.25)
        self.assertEqual(len(self.engine.history), 2)

    def test_emergency_shutdown(self):
        cancelled = self.engine.emergency_shutdown("test reason")
        self.assertTrue(self.cfg.is_kill_switch_active())
        # No open orders to cancel in this test
        self.assertEqual(cancelled, 0)

    def test_emergency_shutdown_cancels_open_orders(self):
        o = self.engine.client.place_order("m1", "BUY", 0.50, 0.10)
        o.status = "OPEN"
        cancelled = self.engine.emergency_shutdown("test")
        self.assertEqual(cancelled, 1)
        self.assertTrue(self.cfg.is_kill_switch_active())

    def test_status_summary_keys(self):
        summary = self.engine.status_summary()
        expected_keys = {"dry_run", "kill_switch_active", "daily_pnl",
                         "max_position_usdc", "max_daily_loss_usdc",
                         "open_orders", "total_attempts", "rejected_attempts"}
        self.assertEqual(set(summary.keys()), expected_keys)

    def test_status_summary_counts_rejections(self):
        self.engine.try_place_order("m1", "BUY", 0.60, 0.50)  # pass
        self.cfg.activate_kill_switch("test")
        self.engine.try_place_order("m2", "BUY", 0.60, 0.50)  # reject
        summary = self.engine.status_summary()
        self.assertEqual(summary["total_attempts"], 2)
        self.assertEqual(summary["rejected_attempts"], 1)


class TestEngineMaxPositionEnforcement(unittest.TestCase):
    """Verify the $1 USDC ceiling cannot be bypassed."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ks_path = os.path.join(self.tmpdir, "KILL_SWITCH")

    def tearDown(self):
        if os.path.exists(self.ks_path):
            os.unlink(self.ks_path)
        os.rmdir(self.tmpdir)

    def test_order_above_one_dollar_rejected(self):
        cfg = BotConfig(
            dry_run=True,
            kill_switch_path=self.ks_path,
            max_position_usdc=999.0,  # try to override — should be clamped
        )
        engine = Engine(cfg)
        attempt = engine.try_place_order("m1", "BUY", 0.50, 1.50)
        self.assertEqual(attempt.gate_result.verdict,
                         GateVerdict.REJECT_POSITION_LIMIT)

    def test_order_at_exactly_one_dollar_passes(self):
        cfg = BotConfig(
            dry_run=True,
            kill_switch_path=self.ks_path,
        )
        engine = Engine(cfg)
        attempt = engine.try_place_order("m1", "BUY", 0.50, 1.00)
        self.assertEqual(attempt.gate_result.verdict, GateVerdict.PASS)


if __name__ == "__main__":
    unittest.main()
