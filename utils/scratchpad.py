#!/usr/bin/env python3
"""
Scratchpad logger - writes structured JSONL to logs/scratchpad/
Auto-creates the directory on first write.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class Scratchpad:
    """
    Writes structured event logs to logs/scratchpad/*.jsonl

    Usage:
        scratchpad = Scratchpad(base_dir="/opt/slimy/pm_updown_bot_bundle")
        scratchpad.log("prior_validation_failed", market="FED-25.JAN", prior=0.62, ...)
    """

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base_dir = Path(base_dir)
        self.log_dir = self.base_dir / "logs" / "scratchpad"
        self._ensure_dir()

    def _ensure_dir(self):
        """Create logs/scratchpad/ directory if it doesn't exist."""
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **kwargs):
        """
        Write a JSONL line to logs/scratchpad/{event}.jsonl

        Args:
            event: Event name (used as filename suffix)
            **kwargs: Fields to include in the log entry
        """
        self._ensure_dir()
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **kwargs,
        }
        filename = self.log_dir / f"{event}.jsonl"
        with open(filename, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def log_prior_validation(self, market: str, prior: float, val_result: dict, passed: bool):
        """
        Convenience: log a prior validation event to prior_validation.jsonl

        Args:
            market: Market ticker/id
            prior: Raw AI prior
            val_result: dict from contract_signals.validate_prior()
            passed: bool — did the validation pass
        """
        self.log(
            "prior_validation",
            market=market,
            prior=prior,
            passed=passed,
            adjusted_prior=val_result.get("adjusted_prior", prior),
            confidence=val_result.get("confidence", 1.0),
            reason=val_result.get("reason", ""),
            flags=val_result.get("flags", []),
        )

    def log_regime(self, regime_info: dict):
        """
        Log regime signal event.

        Args:
            regime_info: dict from fear_regime.get_regime()
        """
        self.log(
            "regime_signal",
            fear_greed_value=regime_info.get("fear_greed_value"),
            vix=regime_info.get("vix"),
            regime=regime_info.get("regime"),
            min_edge_override=regime_info.get("min_edge_override"),
        )

    def log_kelly_ai_prior(self, market: str, question: str, prob: float, source: str = "grok"):
        """
        Log AI prior estimate.

        Args:
            market: Market ticker/id
            question: Market question text
            prob: AI-estimated probability
            source: Which AI model provided the prior
        """
        self.log(
            "ai_prior",
            market=market,
            question=question,
            prob=prob,
            source=source,
        )