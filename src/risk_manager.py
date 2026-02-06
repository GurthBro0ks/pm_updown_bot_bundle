"""
Risk Manager — orchestrates Kelly sizing, VaR checks, and Monte Carlo
validation into a single decision pipeline.

Usage:
    from src.risk_manager import RiskManager
    rm = RiskManager(config)
    decision = rm.evaluate_trade(prob_win=0.70, market_prob=0.55)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from src.config import BotConfig, DEFAULT_CONFIG
from src.kelly import KellyResult, size_bet
from src.monte_carlo import SimulationResult, run_simulation
from src.var import (
    Position,
    VaRResult,
    check_daily_loss,
    check_drawdown,
    check_stop_loss,
    parametric_var,
)


@dataclass(frozen=True)
class TradeDecision:
    """Final go / no-go decision for a proposed trade."""

    action: str               # "TRADE", "SKIP", "HALT"
    reason: str               # human-readable rationale
    kelly: KellyResult
    var: VaRResult
    stop_loss_triggered: bool = False
    daily_loss_halt: bool = False
    drawdown_halt: bool = False


class RiskManager:
    """Stateful risk manager that tracks portfolio and enforces limits."""

    def __init__(self, config: BotConfig | None = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.bankroll = self.config.bankroll
        self.peak_equity = self.bankroll
        self.positions: List[Position] = []
        self.daily_pnl: float = 0.0
        self._mc_result: Optional[SimulationResult] = None

    # ------------------------------------------------------------------
    # Core decision
    # ------------------------------------------------------------------
    def evaluate_trade(
        self,
        prob_win: float,
        market_prob: float,
        market_id: str = "",
        category: str = "",
    ) -> TradeDecision:
        """Decide whether to enter a new trade and at what size.

        Runs through Kelly → VaR → portfolio checks in sequence.
        """
        # 1. Kelly sizing.
        kelly_res = size_bet(
            prob_win=prob_win,
            market_prob=market_prob,
            bankroll=self.bankroll,
            config=self.config,
        )

        # 2. Check daily loss halt.
        if check_daily_loss(self.daily_pnl, self.bankroll, self.config):
            return TradeDecision(
                action="HALT",
                reason="daily loss limit breached",
                kelly=kelly_res,
                var=self._current_var(),
                daily_loss_halt=True,
            )

        # 3. Check drawdown halt.
        if check_drawdown(self.bankroll, self.peak_equity, self.config):
            return TradeDecision(
                action="HALT",
                reason="max drawdown breached",
                kelly=kelly_res,
                var=self._current_var(),
                drawdown_halt=True,
            )

        # 4. If Kelly says no-trade, skip.
        if kelly_res.bet_size <= 0:
            return TradeDecision(
                action="SKIP",
                reason=f"insufficient edge ({kelly_res.edge:.2%})",
                kelly=kelly_res,
                var=self._current_var(),
            )

        # 5. VaR / portfolio-level check.
        # Create a hypothetical position to see impact.
        hypo_pos = Position(
            market_id=market_id or "PROPOSED",
            side="YES",
            entry_price=market_prob,
            quantity=kelly_res.bet_size / market_prob if market_prob > 0 else 0,
            current_price=market_prob,
            category=category,
        )
        var_res = parametric_var(
            self.positions + [hypo_pos],
            self.bankroll,
            self.config,
        )

        if var_res.breaches:
            return TradeDecision(
                action="SKIP",
                reason=f"VaR breaches: {'; '.join(var_res.breaches)}",
                kelly=kelly_res,
                var=var_res,
            )

        return TradeDecision(
            action="TRADE",
            reason=f"edge={kelly_res.edge:.2%}, bet=${kelly_res.bet_size:.2f}",
            kelly=kelly_res,
            var=var_res,
        )

    # ------------------------------------------------------------------
    # Portfolio management
    # ------------------------------------------------------------------
    def open_position(self, position: Position) -> None:
        self.positions.append(position)

    def close_position(self, market_id: str, settlement_price: float) -> float:
        """Close a position and return the realised P&L."""
        pos = next((p for p in self.positions if p.market_id == market_id), None)
        if pos is None:
            return 0.0

        if pos.side == "YES":
            pnl = pos.quantity * (settlement_price - pos.entry_price)
        else:
            pnl = pos.quantity * (pos.entry_price - settlement_price)

        self.bankroll += pnl
        self.daily_pnl += pnl
        if self.bankroll > self.peak_equity:
            self.peak_equity = self.bankroll
        self.positions = [p for p in self.positions if p.market_id != market_id]
        return round(pnl, 2)

    def reset_daily_pnl(self) -> None:
        self.daily_pnl = 0.0

    # ------------------------------------------------------------------
    # Monte Carlo validation
    # ------------------------------------------------------------------
    def validate_strategy(
        self, win_rate: float = 0.58, avg_edge: float = 0.08
    ) -> SimulationResult:
        """Run MC simulation to validate current config targets $1 k+ ROI."""
        self._mc_result = run_simulation(
            win_rate=win_rate,
            avg_edge=avg_edge,
            config=self.config,
        )
        return self._mc_result

    @property
    def mc_result(self) -> Optional[SimulationResult]:
        return self._mc_result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _current_var(self) -> VaRResult:
        return parametric_var(self.positions, self.bankroll, self.config)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def positions_needing_stop_loss(self) -> List[Position]:
        return [p for p in self.positions if check_stop_loss(p, self.config)]

    @property
    def num_open_positions(self) -> int:
        return len(self.positions)

    @property
    def total_exposure(self) -> float:
        return sum(p.cost_basis for p in self.positions)

    @property
    def portfolio_risk_pct(self) -> float:
        return self.total_exposure / self.bankroll if self.bankroll > 0 else 0.0
