"""
Bot configuration — safety limits and runtime parameters.

All monetary values are in USDC.  The micro-live profile is designed for a
$1 USDC maximum-exposure test run with hard-coded ceilings that CANNOT be
overridden by environment variables alone (the code enforces absolute caps).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Absolute hard caps — these are *code-level* maximums that no config file or
# env-var can exceed.  They exist so that even a misconfigured deployment
# cannot risk more than the stated amount.
# ---------------------------------------------------------------------------
ABSOLUTE_MAX_POSITION_USDC: float = 1.00
ABSOLUTE_MAX_DAILY_LOSS_USDC: float = 1.00
ABSOLUTE_MAX_OPEN_ORDERS: int = 3

# ---------------------------------------------------------------------------
# Kill-switch file — when this file exists the bot refuses to place any new
# orders and cancels all open ones.  Creating the file is as simple as:
#     touch KILL_SWITCH
# Removing it re-enables trading:
#     rm KILL_SWITCH
# ---------------------------------------------------------------------------
DEFAULT_KILL_SWITCH_PATH = "KILL_SWITCH"


@dataclass
class BotConfig:
    """Runtime configuration with enforced safety ceilings."""

    # --- mode ---
    dry_run: bool = True  # True = stubs only, no real API calls

    # --- safety limits (user-tunable up to the absolute caps) ---
    max_position_usdc: float = 1.00
    max_daily_loss_usdc: float = 1.00
    max_open_orders: int = 3

    # --- kill switch ---
    kill_switch_path: str = DEFAULT_KILL_SWITCH_PATH

    # --- API (only used when dry_run=False) ---
    api_base_url: str = "https://clob.polymarket.com"
    api_timeout_s: float = 10.0

    # --- internal bookkeeping (not user-configurable) ---
    _daily_pnl: float = field(default=0.0, repr=False)

    # --------------------------------------------------------------------- #
    # Enforce hard caps on construction
    # --------------------------------------------------------------------- #
    def __post_init__(self) -> None:
        self.max_position_usdc = min(self.max_position_usdc, ABSOLUTE_MAX_POSITION_USDC)
        self.max_daily_loss_usdc = min(self.max_daily_loss_usdc, ABSOLUTE_MAX_DAILY_LOSS_USDC)
        self.max_open_orders = min(self.max_open_orders, ABSOLUTE_MAX_OPEN_ORDERS)

        if self.max_position_usdc <= 0:
            raise ValueError("max_position_usdc must be > 0")
        if self.max_daily_loss_usdc <= 0:
            raise ValueError("max_daily_loss_usdc must be > 0")
        if self.max_open_orders <= 0:
            raise ValueError("max_open_orders must be > 0")

    # --------------------------------------------------------------------- #
    # Kill-switch helpers
    # --------------------------------------------------------------------- #
    def is_kill_switch_active(self) -> bool:
        """Return True when the kill-switch file exists on disk."""
        return Path(self.kill_switch_path).exists()

    def activate_kill_switch(self, reason: str = "") -> None:
        """Create the kill-switch file, optionally recording a reason."""
        Path(self.kill_switch_path).write_text(
            reason or "kill-switch activated\n"
        )

    def deactivate_kill_switch(self) -> None:
        """Remove the kill-switch file if it exists."""
        p = Path(self.kill_switch_path)
        if p.exists():
            p.unlink()

    # --------------------------------------------------------------------- #
    # Daily-loss tracking
    # --------------------------------------------------------------------- #
    def record_pnl(self, amount: float) -> None:
        """Record a P&L change.  Activates the kill-switch if daily loss
        limit is breached."""
        self._daily_pnl += amount
        if self._daily_pnl <= -self.max_daily_loss_usdc:
            self.activate_kill_switch(
                f"daily loss limit breached: PnL={self._daily_pnl:.4f} "
                f"limit=-{self.max_daily_loss_usdc:.4f}\n"
            )

    def reset_daily_pnl(self) -> None:
        self._daily_pnl = 0.0

    # --------------------------------------------------------------------- #
    # Factory helpers
    # --------------------------------------------------------------------- #
    @classmethod
    def micro_live(cls) -> "BotConfig":
        """Pre-baked config for the $1 USDC micro-live test."""
        return cls(
            dry_run=False,
            max_position_usdc=1.00,
            max_daily_loss_usdc=1.00,
            max_open_orders=3,
        )

    @classmethod
    def dry_run_default(cls) -> "BotConfig":
        """Pre-baked config for stub / dry-run testing."""
        return cls(dry_run=True)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "max_position_usdc": self.max_position_usdc,
            "max_daily_loss_usdc": self.max_daily_loss_usdc,
            "max_open_orders": self.max_open_orders,
            "kill_switch_path": self.kill_switch_path,
            "kill_switch_active": self.is_kill_switch_active(),
            "api_base_url": self.api_base_url,
        }
