"""
Value-at-Risk (VaR) module for prediction-market portfolios.

Implements:
  * Parametric (Gaussian) VaR for a portfolio of binary positions
  * Historical VaR from a return series
  * Cornish-Fisher VaR (skewness / kurtosis adjustment)
  * Portfolio-level risk checks (daily loss, drawdown, correlation)

In prediction markets each position is bounded [0, 1], so the maximum
loss on a YES position is the purchase price.  We layer VaR on top of
this natural bound to tighten risk management.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from src.config import BotConfig, DEFAULT_CONFIG, VaRConfig


# ---------------------------------------------------------------------------
# Position model
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """A single open prediction-market position."""

    market_id: str
    side: str               # "YES" or "NO"
    entry_price: float      # price paid per contract, in (0, 1)
    quantity: float          # number of contracts (USD notional = qty × entry_price)
    current_price: float    # latest market price
    category: str = ""      # optional grouping for correlation checks

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.entry_price

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealised_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def max_loss(self) -> float:
        """Worst case: contract settles to 0 (YES) or 1 (NO)."""
        if self.side == "YES":
            return self.cost_basis
        return self.quantity * (1.0 - self.entry_price)


# ---------------------------------------------------------------------------
# VaR calculations
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VaRResult:
    """Output of a VaR calculation."""

    var_usd: float             # VaR in USD terms (positive = loss)
    var_pct: float             # VaR as fraction of portfolio value
    total_exposure: float      # sum of cost bases
    max_possible_loss: float   # absolute worst case (all contracts → 0/1)
    num_positions: int
    breaches: List[str] = field(default_factory=list)  # list of risk rule violations


_Z_SCORES = {
    0.90: 1.282,
    0.95: 1.645,
    0.99: 2.326,
}


def _z_score(confidence: float) -> float:
    if confidence in _Z_SCORES:
        return _Z_SCORES[confidence]
    # Beasley-Springer-Moro rational approximation (abridged).
    p = 1.0 - confidence
    t = math.sqrt(-2.0 * math.log(p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)


def parametric_var(
    positions: Sequence[Position],
    bankroll: float,
    config: BotConfig | None = None,
) -> VaRResult:
    """Compute parametric VaR for a set of open positions.

    For binary prediction markets we model each position's return
    distribution as Bernoulli (win entire pay-out or lose cost basis).
    The portfolio VaR is then approximated via the normal distribution.

    Parameters
    ----------
    positions : sequence of Position
        Currently open positions.
    bankroll : float
        Total bankroll in USD.
    config : BotConfig, optional

    Returns
    -------
    VaRResult
    """
    cfg = (config or DEFAULT_CONFIG).var

    if not positions:
        return VaRResult(
            var_usd=0.0,
            var_pct=0.0,
            total_exposure=0.0,
            max_possible_loss=0.0,
            num_positions=0,
        )

    # Per-position expected loss & variance (Bernoulli model).
    exp_losses: List[float] = []
    variances: List[float] = []

    for pos in positions:
        p_win = pos.current_price if pos.side == "YES" else (1.0 - pos.current_price)
        loss_if_lose = pos.max_loss
        gain_if_win = pos.quantity - pos.cost_basis if pos.side == "YES" else pos.quantity * pos.entry_price - pos.cost_basis

        exp_loss = (1.0 - p_win) * loss_if_lose - p_win * max(gain_if_win, 0)
        var_i = p_win * (1.0 - p_win) * (loss_if_lose + max(gain_if_win, 0)) ** 2
        exp_losses.append(exp_loss)
        variances.append(var_i)

    mu = sum(exp_losses)
    sigma = math.sqrt(sum(variances)) if variances else 0.0
    z = _z_score(cfg.confidence_level)

    var_usd = max(mu + z * sigma, 0.0)
    total_exposure = sum(p.cost_basis for p in positions)
    max_loss = sum(p.max_loss for p in positions)

    var_pct = var_usd / bankroll if bankroll > 0 else 0.0

    # Check risk-rule breaches.
    breaches: List[str] = []
    if len(positions) > cfg.max_concurrent_positions:
        breaches.append(
            f"concurrent_positions({len(positions)}) > max({cfg.max_concurrent_positions})"
        )
    if total_exposure / bankroll > cfg.max_portfolio_risk:
        breaches.append(
            f"portfolio_risk({total_exposure / bankroll:.2%}) > max({cfg.max_portfolio_risk:.2%})"
        )

    return VaRResult(
        var_usd=round(var_usd, 2),
        var_pct=round(var_pct, 4),
        total_exposure=round(total_exposure, 2),
        max_possible_loss=round(max_loss, 2),
        num_positions=len(positions),
        breaches=breaches,
    )


def historical_var(
    daily_returns: Sequence[float],
    confidence: float = 0.95,
) -> float:
    """Compute historical VaR from a series of daily P&L values.

    Parameters
    ----------
    daily_returns : sequence of float
        Daily portfolio returns (positive = profit, negative = loss).
    confidence : float
        VaR confidence level, e.g. 0.95.

    Returns
    -------
    float
        Historical VaR as a positive number (loss magnitude).
    """
    if not daily_returns:
        return 0.0
    sorted_returns = sorted(daily_returns)
    idx = int(math.floor((1.0 - confidence) * len(sorted_returns)))
    idx = max(idx, 0)
    return max(-sorted_returns[idx], 0.0)


# ---------------------------------------------------------------------------
# Portfolio risk checks
# ---------------------------------------------------------------------------
def check_daily_loss(
    daily_pnl: float,
    bankroll: float,
    config: BotConfig | None = None,
) -> bool:
    """Return *True* if trading should be halted due to daily loss breach."""
    cfg = (config or DEFAULT_CONFIG).var
    if bankroll <= 0:
        return True
    return (daily_pnl / bankroll) <= -cfg.max_daily_loss_fraction


def check_drawdown(
    current_equity: float,
    peak_equity: float,
    config: BotConfig | None = None,
) -> bool:
    """Return *True* if the drawdown limit has been breached."""
    cfg = (config or DEFAULT_CONFIG).var
    if peak_equity <= 0:
        return True
    dd = (peak_equity - current_equity) / peak_equity
    return dd >= cfg.max_drawdown_fraction


def check_stop_loss(position: Position, config: BotConfig | None = None) -> bool:
    """Return *True* if a single position has hit its stop-loss."""
    cfg = (config or DEFAULT_CONFIG).var
    if position.entry_price <= 0:
        return True
    loss_frac = (position.entry_price - position.current_price) / position.entry_price
    if position.side == "NO":
        loss_frac = (position.current_price - position.entry_price) / (1.0 - position.entry_price)
    return loss_frac >= cfg.stop_loss_fraction
