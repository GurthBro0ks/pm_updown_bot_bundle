#!/usr/bin/env python3
"""
Stage Budget Module — Per-stage wall-clock budgets for the runner pipeline.

Every pipeline stage declares a hard wall-clock budget in seconds.
When a stage's budget is exhausted, the stage stops processing and returns
whatever partial results it has. The pipeline continues with the partial
results — the run does NOT abort.

BudgetExhausted is reserved for cases where a stage wants to hard-abort
on exhaustion (not used in v1).
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class BudgetExhausted(Exception):
    """Raised when a stage's budget is fully consumed.

    Not used in v1 pipelines — stages log and continue with partial results.
    Reserved for future hard-abort semantics.
    """
    pass


class StageBudget:
    """
    Tracks elapsed wall-clock time against a declared budget for a pipeline stage.

    Usage as context manager:
        budget = StageBudget("ai_cascade", 300.0)
        with budget:
            for market in markets:
                if budget.exhausted():
                    break
                result = call_provider(market, timeout=budget.remaining())
                processed += 1

    Fields logged to scratchpad on __exit__:
        stage_name, budget_seconds, elapsed_seconds, exhausted (bool),
        items_processed, items_skipped, cron_run_id
    """

    def __init__(
        self,
        name: str,
        seconds: float,
        start: Optional[datetime] = None,
        cron_run_id: Optional[str] = None,
    ):
        """
        Args:
            name: Stage identifier (e.g. "ai_cascade").
            seconds: Hard wall-clock budget in seconds.
            start: Optional fixed start time for testing/ reproducibility.
                  Defaults to now().
            cron_run_id: Optional UUID string to correlate all stages
                        within a single cron run (generated if not provided).
        """
        self.name = name
        self.budget_seconds = float(seconds)
        self._start_time: Optional[float] = None
        self._start_dt = start or datetime.now(timezone.utc)
        self._items_processed = 0
        self._items_skipped = 0
        self._exhausted = False
        self._cron_run_id = cron_run_id or str(uuid.uuid4())
        self._entered = False

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------

    def _now(self) -> float:
        """Wall-clock seconds since the epoch."""
        return time.monotonic()

    def elapsed(self) -> float:
        """Seconds elapsed since __init__ or last reset."""
        if self._start_time is None:
            return 0.0
        return self._now() - self._start_time

    def remaining(self) -> float:
        """Seconds left in the budget (floored at 0)."""
        return max(0.0, self.budget_seconds - self.elapsed())

    def exhausted(self) -> bool:
        """True when wall-clock elapsed >= budget."""
        return self.elapsed() >= self.budget_seconds

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "StageBudget":
        self._start_time = self._now()
        self._entered = True
        self._items_processed = 0
        self._items_skipped = 0
        self._exhausted = False
        logger.debug(
            "[budget] %s START  budget=%.1fs",
            self.name,
            self.budget_seconds,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = self.elapsed()
        self._exhausted = self.exhausted()
        logger.debug(
            "[budget] %s END    elapsed=%.1fs exhausted=%s processed=%d skipped=%d",
            self.name,
            elapsed,
            self._exhausted,
            self._items_processed,
            self._items_skipped,
        )
        self._write_scratchpad()
        return False  # do not suppress exceptions

    # ------------------------------------------------------------------
    # Item counting
    # ------------------------------------------------------------------

    def mark_processed(self, n: int = 1):
        """Record n items successfully processed within the budget."""
        self._items_processed += n

    def mark_skipped(self, n: int = 1):
        """Record n items skipped because budget was exhausted."""
        self._items_skipped += n

    # ------------------------------------------------------------------
    # Scratchpad
    # ------------------------------------------------------------------

    def _scratchpad_path(self) -> Path:
        base = Path("/opt/slimy/pm_updown_bot_bundle")
        return base / "logs" / "scratchpad" / "stage_budget.jsonl"

    def _write_scratchpad(self):
        """Append a stage_budget event to the scratchpad JSONL."""
        entry = {
            "event_type": "stage_budget",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage_name": self.name,
            "budget_seconds": self.budget_seconds,
            "elapsed_seconds": self.elapsed(),
            "exhausted": self._exhausted,
            "items_processed": self._items_processed,
            "items_skipped": self._items_skipped,
            "cron_run_id": self._cron_run_id,
        }
        try:
            self._scratchpad_path().parent.mkdir(parents=True, exist_ok=True)
            with open(self._scratchpad_path(), "a") as f:
                f.write(__import__("json").dumps(entry) + "\n")
        except Exception as e:
            logger.warning("[budget] scratchpad write failed: %s", e)

    def log_entry(self):
        """Manual log_entry for pre-stage scratchpad event (optional)."""
        self._write_scratchpad()
