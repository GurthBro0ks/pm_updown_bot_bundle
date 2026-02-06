"""Tests for bot.config — safety limits, kill-switch, daily-loss tracking."""

import os
import tempfile
import unittest
from pathlib import Path

from bot.config import (
    ABSOLUTE_MAX_DAILY_LOSS_USDC,
    ABSOLUTE_MAX_OPEN_ORDERS,
    ABSOLUTE_MAX_POSITION_USDC,
    BotConfig,
)


class TestAbsoluteCaps(unittest.TestCase):
    """Hard caps must never be exceeded regardless of user input."""

    def test_caps_are_one_dollar(self):
        self.assertEqual(ABSOLUTE_MAX_POSITION_USDC, 1.00)
        self.assertEqual(ABSOLUTE_MAX_DAILY_LOSS_USDC, 1.00)
        self.assertEqual(ABSOLUTE_MAX_OPEN_ORDERS, 3)

    def test_user_values_clamped_to_caps(self):
        cfg = BotConfig(
            max_position_usdc=100.0,
            max_daily_loss_usdc=50.0,
            max_open_orders=20,
        )
        self.assertLessEqual(cfg.max_position_usdc, ABSOLUTE_MAX_POSITION_USDC)
        self.assertLessEqual(cfg.max_daily_loss_usdc, ABSOLUTE_MAX_DAILY_LOSS_USDC)
        self.assertLessEqual(cfg.max_open_orders, ABSOLUTE_MAX_OPEN_ORDERS)

    def test_values_within_caps_preserved(self):
        cfg = BotConfig(
            max_position_usdc=0.50,
            max_daily_loss_usdc=0.25,
            max_open_orders=2,
        )
        self.assertEqual(cfg.max_position_usdc, 0.50)
        self.assertEqual(cfg.max_daily_loss_usdc, 0.25)
        self.assertEqual(cfg.max_open_orders, 2)

    def test_zero_position_rejected(self):
        with self.assertRaises(ValueError):
            BotConfig(max_position_usdc=0)

    def test_negative_daily_loss_rejected(self):
        with self.assertRaises(ValueError):
            BotConfig(max_daily_loss_usdc=-1)

    def test_zero_open_orders_rejected(self):
        with self.assertRaises(ValueError):
            BotConfig(max_open_orders=0)


class TestKillSwitch(unittest.TestCase):
    """Kill-switch file-based mechanism."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ks_path = os.path.join(self.tmpdir, "KILL_SWITCH")
        self.cfg = BotConfig(kill_switch_path=self.ks_path)

    def tearDown(self):
        if os.path.exists(self.ks_path):
            os.unlink(self.ks_path)
        os.rmdir(self.tmpdir)

    def test_not_active_by_default(self):
        self.assertFalse(self.cfg.is_kill_switch_active())

    def test_activate_creates_file(self):
        self.cfg.activate_kill_switch("test reason")
        self.assertTrue(self.cfg.is_kill_switch_active())
        self.assertTrue(os.path.exists(self.ks_path))
        content = Path(self.ks_path).read_text()
        self.assertIn("test reason", content)

    def test_deactivate_removes_file(self):
        self.cfg.activate_kill_switch()
        self.assertTrue(self.cfg.is_kill_switch_active())
        self.cfg.deactivate_kill_switch()
        self.assertFalse(self.cfg.is_kill_switch_active())

    def test_deactivate_noop_when_not_active(self):
        self.cfg.deactivate_kill_switch()  # should not raise
        self.assertFalse(self.cfg.is_kill_switch_active())


class TestDailyLossTracking(unittest.TestCase):
    """Daily loss tracking and auto kill-switch on breach."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ks_path = os.path.join(self.tmpdir, "KILL_SWITCH")
        self.cfg = BotConfig(
            kill_switch_path=self.ks_path,
            max_daily_loss_usdc=1.00,
        )

    def tearDown(self):
        if os.path.exists(self.ks_path):
            os.unlink(self.ks_path)
        os.rmdir(self.tmpdir)

    def test_pnl_starts_at_zero(self):
        self.assertEqual(self.cfg._daily_pnl, 0.0)

    def test_profit_does_not_trigger_kill_switch(self):
        self.cfg.record_pnl(0.50)
        self.assertFalse(self.cfg.is_kill_switch_active())

    def test_loss_within_limit_no_kill_switch(self):
        self.cfg.record_pnl(-0.50)
        self.assertFalse(self.cfg.is_kill_switch_active())

    def test_loss_at_limit_triggers_kill_switch(self):
        self.cfg.record_pnl(-1.00)
        self.assertTrue(self.cfg.is_kill_switch_active())

    def test_cumulative_loss_triggers_kill_switch(self):
        self.cfg.record_pnl(-0.30)
        self.cfg.record_pnl(-0.30)
        self.assertFalse(self.cfg.is_kill_switch_active())
        self.cfg.record_pnl(-0.40)
        self.assertTrue(self.cfg.is_kill_switch_active())

    def test_reset_clears_pnl(self):
        self.cfg.record_pnl(-0.50)
        self.cfg.reset_daily_pnl()
        self.assertEqual(self.cfg._daily_pnl, 0.0)


class TestFactoryMethods(unittest.TestCase):
    def test_micro_live_is_not_dry_run(self):
        cfg = BotConfig.micro_live()
        self.assertFalse(cfg.dry_run)
        self.assertEqual(cfg.max_position_usdc, 1.00)

    def test_dry_run_default_is_dry_run(self):
        cfg = BotConfig.dry_run_default()
        self.assertTrue(cfg.dry_run)

    def test_to_dict_returns_expected_keys(self):
        cfg = BotConfig.dry_run_default()
        d = cfg.to_dict()
        for key in ("dry_run", "max_position_usdc", "max_daily_loss_usdc",
                     "max_open_orders", "kill_switch_active"):
            self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
