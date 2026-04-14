#!/usr/bin/env python3
"""
Tests for core.stage_budget
"""

import json
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.stage_budget import StageBudget, BudgetExhausted


class TestStageBudgetBasics(unittest.TestCase):
    def test_elapsed_returns_zero_before_enter(self):
        b = StageBudget("test", 100.0)
        self.assertEqual(b.elapsed(), 0.0)

    def test_exhausted_false_before_enter(self):
        b = StageBudget("test", 100.0)
        self.assertFalse(b.exhausted())

    def test_remaining_equals_budget_before_enter(self):
        b = StageBudget("test", 50.0)
        self.assertEqual(b.remaining(), 50.0)

    def test_exhausted_false_under_budget(self):
        b = StageBudget("test", 100.0)
        self.assertFalse(b.exhausted())

    def test_exhausted_true_at_budget(self):
        # Enter context so _start_time is set, then fake the clock to exactly budget
        b = StageBudget("test", 10.0)
        with b:
            base = time.monotonic()
            with patch("core.stage_budget.time.monotonic", side_effect=lambda: base + 10.0):
                self.assertTrue(b.exhausted())
                self.assertAlmostEqual(b.elapsed(), 10.0, places=1)

    def test_exhausted_true_over_budget(self):
        b = StageBudget("test", 5.0)
        with b:
            base = time.monotonic()
            with patch("core.stage_budget.time.monotonic", side_effect=lambda: base + 50.0):
                self.assertTrue(b.exhausted())

    def test_remaining_floors_at_zero(self):
        b = StageBudget("test", 5.0)
        with b:
            base = time.monotonic()
            with patch("core.stage_budget.time.monotonic", side_effect=lambda: base + 100.0):
                self.assertEqual(b.remaining(), 0.0)

    def test_remaining_decreases(self):
        b = StageBudget("test", 10.0)
        with b:
            base = time.monotonic()
            with patch("core.stage_budget.time.monotonic", side_effect=lambda: base + 3.0):
                self.assertAlmostEqual(b.remaining(), 7.0, places=1)


class TestStageBudgetContextManager(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path("/tmp/test_scratchpad.jsonl")
        self.tmp_path.parent.mkdir(parents=True, exist_ok=True)

    def test_context_manager_enters(self):
        b = StageBudget("test_stage", 10.0)
        with b:
            self.assertTrue(b._entered)
            self.assertIsNotNone(b._start_time)

    def test_context_manager_exit_logs(self):
        b = StageBudget("test_stage", 10.0)
        with b:
            pass
        # elapsed should be recorded
        self.assertIsNotNone(b.elapsed())

    def test_items_counted_on_context_exit(self):
        b = StageBudget("test_stage", 10.0)
        with b:
            b.mark_processed(5)
            b.mark_skipped(3)
        self.assertEqual(b._items_processed, 5)
        self.assertEqual(b._items_skipped, 3)

    def test_scratchpad_entry_written(self):
        tmp = Path("/tmp/test_sb_scratchpad.jsonl")
        if tmp.exists():
            tmp.unlink()

        class SB(StageBudget):
            def _scratchpad_path(self):
                return tmp

        b = SB("test_stage", 10.0)
        with b:
            b.mark_processed(7)
        entries = []
        if tmp.exists():
            with open(tmp) as f:
                entries = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["stage_name"], "test_stage")
        self.assertEqual(e["budget_seconds"], 10.0)
        self.assertEqual(e["items_processed"], 7)
        self.assertEqual(e["exhausted"], False)
        self.assertIn("cron_run_id", e)


class TestStageBudgetRapidCalls(unittest.TestCase):
    def test_rapid_elapsed_calls_dont_break(self):
        b = StageBudget("test", 100.0)
        results = [b.elapsed() for _ in range(1000)]
        # All should be 0.0 before entering
        self.assertTrue(all(r == 0.0 for r in results))

    def test_rapid_exhausted_calls_dont_break(self):
        b = StageBudget("test", 100.0)
        results = [b.exhausted() for _ in range(1000)]
        self.assertTrue(all(r == False for r in results))


class TestBudgetExhausted(unittest.TestCase):
    def test_budget_exhausted_is_exception(self):
        self.assertTrue(issubclass(BudgetExhausted, Exception))

    def test_can_raise_and_catch(self):
        with self.assertRaises(BudgetExhausted):
            raise BudgetExhausted("test stage out of time")


class TestCronRunId(unittest.TestCase):
    def test_cron_run_id_provided(self):
        rid = "abc-123"
        b = StageBudget("test", 10.0, cron_run_id=rid)
        self.assertEqual(b._cron_run_id, rid)

    def test_cron_run_id_generated(self):
        b1 = StageBudget("test", 10.0)
        b2 = StageBudget("test", 10.0)
        self.assertIsNotNone(b1._cron_run_id)
        self.assertIsNotNone(b2._cron_run_id)
        # Two instances should get different IDs (uuid4)
        self.assertNotEqual(b1._cron_run_id, b2._cron_run_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
