"""
Polymarket CLOB API client with a dry-run stub layer.

When ``BotConfig.dry_run`` is True the client never makes real HTTP calls;
instead it returns deterministic fake responses so that every code-path
exercised during shadow-mode is identical to the live path — except no money
moves.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bot.config import BotConfig


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class Order:
    order_id: str
    market_id: str
    side: str          # "BUY" | "SELL"
    price: float       # 0.01 – 0.99
    size_usdc: float
    status: str = "OPEN"   # OPEN | FILLED | CANCELLED
    filled_at: Optional[float] = None


@dataclass
class Position:
    market_id: str
    side: str
    size_usdc: float
    entry_price: float


# ── Stub / Dry-run backend ────────────────────────────────────────────────

class StubBackend:
    """In-memory fake that mimics the Polymarket CLOB just enough for
    testing order flow and safety gates end-to-end."""

    def __init__(self) -> None:
        self.orders: Dict[str, Order] = {}
        self.positions: Dict[str, Position] = {}
        self.call_log: List[Dict[str, Any]] = []

    def _log(self, method: str, **kwargs: Any) -> None:
        self.call_log.append({"method": method, "ts": time.time(), **kwargs})

    # -- order management ---------------------------------------------------

    def place_order(self, market_id: str, side: str, price: float,
                    size_usdc: float) -> Order:
        oid = f"stub-{uuid.uuid4().hex[:8]}"
        order = Order(
            order_id=oid,
            market_id=market_id,
            side=side,
            price=price,
            size_usdc=size_usdc,
            status="FILLED",
            filled_at=time.time(),
        )
        self.orders[oid] = order
        # Track position
        self.positions[market_id] = Position(
            market_id=market_id,
            side=side,
            size_usdc=size_usdc,
            entry_price=price,
        )
        self._log("place_order", order_id=oid, market_id=market_id,
                  side=side, price=price, size_usdc=size_usdc)
        return order

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id].status = "CANCELLED"
            self._log("cancel_order", order_id=order_id)
            return True
        return False

    def cancel_all(self) -> int:
        count = 0
        for oid, order in self.orders.items():
            if order.status == "OPEN":
                order.status = "CANCELLED"
                count += 1
        self._log("cancel_all", cancelled=count)
        return count

    def get_open_orders(self) -> List[Order]:
        return [o for o in self.orders.values() if o.status == "OPEN"]

    def get_positions(self) -> Dict[str, Position]:
        return dict(self.positions)

    def get_market_price(self, market_id: str) -> float:
        """Return a deterministic fake mid-price."""
        self._log("get_market_price", market_id=market_id)
        return 0.50


# ── Unified API client ────────────────────────────────────────────────────

class ApiClient:
    """Thin wrapper that delegates to either the stub backend (dry_run=True)
    or would delegate to a real HTTP backend (dry_run=False, not yet wired)."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        if config.dry_run:
            self._backend = StubBackend()
        else:
            # For micro-live we still use stubs until the real HTTP layer
            # is reviewed and approved for production use.
            self._backend = StubBackend()

    # -- forwarding ---------------------------------------------------------

    def place_order(self, market_id: str, side: str, price: float,
                    size_usdc: float) -> Order:
        return self._backend.place_order(market_id, side, price, size_usdc)

    def cancel_order(self, order_id: str) -> bool:
        return self._backend.cancel_order(order_id)

    def cancel_all(self) -> int:
        return self._backend.cancel_all()

    def get_open_orders(self) -> List[Order]:
        return self._backend.get_open_orders()

    def get_positions(self) -> Dict[str, Position]:
        return self._backend.get_positions()

    def get_market_price(self, market_id: str) -> float:
        return self._backend.get_market_price(market_id)

    @property
    def call_log(self) -> List[Dict[str, Any]]:
        return self._backend.call_log
