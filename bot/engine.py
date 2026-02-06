"""
Core trading engine with pre-trade safety gates.

Every order attempt passes through a chain of safety checks before it
reaches the API client.  If *any* gate rejects the order, the attempt is
logged and silently dropped (fail-closed).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional

from bot.api_client import ApiClient, Order
from bot.config import BotConfig

logger = logging.getLogger(__name__)


# ── Gate verdicts ─────────────────────────────────────────────────────────

class GateVerdict(Enum):
    PASS = auto()
    REJECT_KILL_SWITCH = auto()
    REJECT_POSITION_LIMIT = auto()
    REJECT_DAILY_LOSS = auto()
    REJECT_OPEN_ORDER_LIMIT = auto()
    REJECT_INVALID_PARAMS = auto()


@dataclass
class GateResult:
    verdict: GateVerdict
    reason: str = ""


# ── Safety gate chain ─────────────────────────────────────────────────────

def check_gates(config: BotConfig, client: ApiClient,
                size_usdc: float, price: float) -> GateResult:
    """Run every pre-trade safety gate.  Returns on the first rejection."""

    # 1. Kill-switch
    if config.is_kill_switch_active():
        return GateResult(GateVerdict.REJECT_KILL_SWITCH,
                          "kill-switch file is active")

    # 2. Parameter validation
    if size_usdc <= 0 or price <= 0 or price >= 1.0:
        return GateResult(GateVerdict.REJECT_INVALID_PARAMS,
                          f"bad params size={size_usdc} price={price}")

    # 3. Position-size cap
    if size_usdc > config.max_position_usdc:
        return GateResult(
            GateVerdict.REJECT_POSITION_LIMIT,
            f"size {size_usdc} exceeds max {config.max_position_usdc}",
        )

    # 4. Daily-loss cap
    if config._daily_pnl <= -config.max_daily_loss_usdc:
        return GateResult(GateVerdict.REJECT_DAILY_LOSS,
                          f"daily PnL {config._daily_pnl} at/below limit")

    # 5. Open-order cap
    open_orders = client.get_open_orders()
    if len(open_orders) >= config.max_open_orders:
        return GateResult(
            GateVerdict.REJECT_OPEN_ORDER_LIMIT,
            f"{len(open_orders)} open orders >= max {config.max_open_orders}",
        )

    return GateResult(GateVerdict.PASS)


# ── Engine ────────────────────────────────────────────────────────────────

@dataclass
class TradeAttempt:
    market_id: str
    side: str
    price: float
    size_usdc: float
    gate_result: GateResult
    order: Optional[Order] = None
    ts: float = field(default_factory=time.time)


class Engine:
    """Orchestrates the trading loop with safety gates."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.client = ApiClient(config)
        self.history: List[TradeAttempt] = []

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def try_place_order(self, market_id: str, side: str, price: float,
                        size_usdc: float) -> TradeAttempt:
        """Attempt an order.  All safety gates run first."""
        gate = check_gates(self.config, self.client, size_usdc, price)

        attempt = TradeAttempt(
            market_id=market_id,
            side=side,
            price=price,
            size_usdc=size_usdc,
            gate_result=gate,
        )

        if gate.verdict is GateVerdict.PASS:
            order = self.client.place_order(market_id, side, price, size_usdc)
            attempt.order = order
            logger.info("ORDER PLACED %s %s %.4f @ %.2f",
                        market_id, side, size_usdc, price)
        else:
            logger.warning("ORDER REJECTED [%s] %s", gate.verdict.name,
                           gate.reason)

        self.history.append(attempt)
        return attempt

    def emergency_shutdown(self, reason: str = "manual") -> int:
        """Activate kill-switch and cancel all open orders."""
        self.config.activate_kill_switch(f"emergency shutdown: {reason}\n")
        cancelled = self.client.cancel_all()
        logger.warning("EMERGENCY SHUTDOWN: reason=%s cancelled=%d",
                       reason, cancelled)
        return cancelled

    def status_summary(self) -> dict:
        """Human-readable status snapshot."""
        return {
            "dry_run": self.config.dry_run,
            "kill_switch_active": self.config.is_kill_switch_active(),
            "daily_pnl": self.config._daily_pnl,
            "max_position_usdc": self.config.max_position_usdc,
            "max_daily_loss_usdc": self.config.max_daily_loss_usdc,
            "open_orders": len(self.client.get_open_orders()),
            "total_attempts": len(self.history),
            "rejected_attempts": sum(
                1 for a in self.history
                if a.gate_result.verdict is not GateVerdict.PASS
            ),
        }
