"""
Tunable configuration for the prediction-market risk engine.

Parameters are calibrated from research on Polymarket winners, Kalshi bots,
and academic literature (see docs/research.md).

All monetary values are in USD.  Probabilities are in [0, 1].
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Kelly Criterion
# ---------------------------------------------------------------------------
@dataclass
class KellyConfig:
    """Parameters governing Kelly-based position sizing."""

    # Base fractional Kelly multiplier (quarter-Kelly default).
    base_fraction: float = 0.25

    # Edge-based scaling tiers — maps *minimum* edge to the Kelly fraction
    # that should be used when a trade's edge falls into that bucket.
    # Keys are edge lower-bounds (exclusive of previous tier).
    edge_tiers: Dict[float, float] = field(default_factory=lambda: {
        0.05: 0.25,   # 5-10 % edge  → quarter-Kelly
        0.10: 0.35,   # 10-15 % edge → 0.35× Kelly
        0.15: 0.50,   # 15 %+  edge  → half-Kelly (cap)
    })

    # Hard floor / ceiling on any single bet as fraction of bankroll.
    min_bet_fraction: float = 0.005   # 0.5 %
    max_bet_fraction: float = 0.05    # 5 %

    # Minimum edge required to place a trade at all.
    min_edge: float = 0.05            # 5 %

    # Minimum model confidence to act.
    min_confidence: float = 0.60      # 60 %


# ---------------------------------------------------------------------------
# Value-at-Risk
# ---------------------------------------------------------------------------
@dataclass
class VaRConfig:
    """Parameters governing Value-at-Risk constraints."""

    # Maximum fraction of bankroll at risk across all open positions.
    max_portfolio_risk: float = 0.20          # 20 %

    # Maximum number of concurrent open positions.
    max_concurrent_positions: int = 10

    # Maximum pairwise Pearson correlation allowed between any two
    # open positions (prevents correlated blow-ups).
    max_correlation: float = 0.70

    # Daily loss limit — if breached, halt new trades until next day.
    max_daily_loss_fraction: float = 0.02     # 2 %

    # Drawdown from equity peak that triggers a full stop.
    max_drawdown_fraction: float = 0.15       # 15 %

    # Per-position stop-loss expressed as fraction of entry price.
    stop_loss_fraction: float = 0.05          # 5 %

    # Confidence level for parametric VaR (e.g. 95 % or 99 %).
    confidence_level: float = 0.95


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
@dataclass
class MonteCarloConfig:
    """Parameters governing Monte-Carlo simulations."""

    # Number of simulation paths.
    num_simulations: int = 10_000

    # Trading horizon in days.
    horizon_days: int = 365

    # Average trades per day (used to generate trade sequences).
    trades_per_day: float = 2.0

    # The 10th-percentile outcome must retain at least this fraction
    # of the starting bankroll for a strategy to be acceptable.
    min_10th_percentile_retention: float = 0.90   # 90 %

    # Target yearly ROI (absolute USD).  Used for reporting only.
    target_yearly_roi_usd: float = 1_000.0

    # Random seed for reproducibility (None ⇒ random each run).
    seed: int | None = 42


# ---------------------------------------------------------------------------
# Market filters
# ---------------------------------------------------------------------------
@dataclass
class MarketFilterConfig:
    """Filters applied before a market is even considered."""

    min_volume: int = 200
    max_time_to_expiry_days: int = 30
    min_profit_after_fees: float = 0.02   # 2 % net-of-fees


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------
@dataclass
class BotConfig:
    """Aggregates every sub-config into one object."""

    bankroll: float = 5_000.0   # starting bankroll USD

    kelly: KellyConfig = field(default_factory=KellyConfig)
    var: VaRConfig = field(default_factory=VaRConfig)
    monte_carlo: MonteCarloConfig = field(default_factory=MonteCarloConfig)
    market_filter: MarketFilterConfig = field(default_factory=MarketFilterConfig)

    def copy(self) -> "BotConfig":
        return copy.deepcopy(self)


# Convenience singleton — importable across the codebase.
DEFAULT_CONFIG = BotConfig()
