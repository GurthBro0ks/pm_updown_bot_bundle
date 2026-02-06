"""
Monte Carlo simulation engine for prediction-market strategies.

Simulates thousands of trading paths to:
  * Validate that a Kelly-based strategy targets $1 k+ yearly ROI
  * Stress-test drawdown behaviour
  * Produce percentile-based confidence intervals

Each path is a sequence of daily trades; per-trade outcomes are sampled
from a Bernoulli distribution parameterised by the trader's estimated
edge and win-rate.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from src.config import BotConfig, DEFAULT_CONFIG


@dataclass(frozen=True)
class SimulationResult:
    """Aggregated results across all Monte-Carlo paths."""

    num_simulations: int
    horizon_days: int

    # Terminal bankroll statistics.
    mean_final_bankroll: float
    median_final_bankroll: float
    p10_final_bankroll: float      # 10th percentile (worst reasonable case)
    p90_final_bankroll: float      # 90th percentile (best reasonable case)
    min_final_bankroll: float
    max_final_bankroll: float

    # ROI statistics (relative to starting bankroll).
    mean_roi_pct: float
    median_roi_pct: float
    p10_roi_pct: float
    prob_positive_roi: float       # fraction of paths with profit > 0
    prob_target_roi: float         # fraction of paths reaching target ROI

    # Drawdown statistics.
    mean_max_drawdown_pct: float
    median_max_drawdown_pct: float
    p90_max_drawdown_pct: float    # 90th-percentile worst drawdown

    # Risk-adjusted.
    estimated_sharpe: float

    # Raw terminal bankrolls (for downstream analysis / histograms).
    terminal_bankrolls: List[float] = field(default_factory=list, repr=False)


def _percentile(sorted_vals: List[float], pct: float) -> float:
    """Return the *pct*-th percentile of a **sorted** list (0–100 scale)."""
    if not sorted_vals:
        return 0.0
    k = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(k))
    hi = min(lo + 1, len(sorted_vals) - 1)
    weight = k - lo
    return sorted_vals[lo] * (1.0 - weight) + sorted_vals[hi] * weight


def simulate_path(
    bankroll: float,
    win_rate: float,
    avg_edge: float,
    kelly_fraction: float,
    trades_per_day: float,
    horizon_days: int,
    min_bet_frac: float = 0.005,
    max_bet_frac: float = 0.05,
    rng: random.Random | None = None,
) -> Tuple[float, float]:
    """Simulate a single trading path.

    Returns (final_bankroll, max_drawdown_fraction).
    """
    rng = rng or random.Random()
    equity = bankroll
    peak = equity

    total_trades = int(trades_per_day * horizon_days)
    max_dd = 0.0

    for _ in range(total_trades):
        if equity <= 0:
            break

        # Compute raw Kelly fraction for this trade.
        # Odds implied by a market_prob derived from (win_rate - avg_edge).
        market_prob = max(win_rate - avg_edge, 0.01)
        odds = 1.0 / market_prob
        b = odds - 1.0
        q = 1.0 - win_rate
        raw_f = (win_rate * b - q) / b if b > 0 else 0.0
        raw_f = max(raw_f, 0.0)

        scaled_f = raw_f * kelly_fraction
        scaled_f = max(scaled_f, min_bet_frac)
        scaled_f = min(scaled_f, max_bet_frac)

        bet = scaled_f * equity

        # Outcome: win with probability win_rate.
        if rng.random() < win_rate:
            payout = bet * b   # net profit on win
            equity += payout
        else:
            equity -= bet

        # Track drawdown.
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return equity, max_dd


def run_simulation(
    win_rate: float = 0.58,
    avg_edge: float = 0.08,
    config: BotConfig | None = None,
) -> SimulationResult:
    """Run a full Monte-Carlo simulation suite.

    Parameters
    ----------
    win_rate : float
        Assumed long-run win rate (e.g. 0.58 = 58 %).
    avg_edge : float
        Average edge per trade (e.g. 0.08 = 8 %).
    config : BotConfig, optional

    Returns
    -------
    SimulationResult
    """
    cfg = config or DEFAULT_CONFIG
    mc = cfg.monte_carlo
    kc = cfg.kelly

    rng = random.Random(mc.seed) if mc.seed is not None else random.Random()

    finals: List[float] = []
    drawdowns: List[float] = []

    for _ in range(mc.num_simulations):
        fb, mdd = simulate_path(
            bankroll=cfg.bankroll,
            win_rate=win_rate,
            avg_edge=avg_edge,
            kelly_fraction=kc.base_fraction,
            trades_per_day=mc.trades_per_day,
            horizon_days=mc.horizon_days,
            min_bet_frac=kc.min_bet_fraction,
            max_bet_frac=kc.max_bet_fraction,
            rng=random.Random(rng.randint(0, 2**31)),
        )
        finals.append(fb)
        drawdowns.append(mdd)

    # Sort for percentile calculations.
    sorted_finals = sorted(finals)
    sorted_dd = sorted(drawdowns)

    bankroll = cfg.bankroll
    mean_final = sum(finals) / len(finals)
    median_final = _percentile(sorted_finals, 50)
    p10_final = _percentile(sorted_finals, 10)
    p90_final = _percentile(sorted_finals, 90)

    rois = [(f - bankroll) / bankroll * 100 for f in finals]
    sorted_rois = sorted(rois)
    mean_roi = sum(rois) / len(rois)
    median_roi = _percentile(sorted_rois, 50)
    p10_roi = _percentile(sorted_rois, 10)

    prob_pos = sum(1 for r in rois if r > 0) / len(rois)
    target_roi_pct = (mc.target_yearly_roi_usd / bankroll) * 100
    prob_target = sum(1 for r in rois if r >= target_roi_pct) / len(rois)

    mean_dd = sum(drawdowns) / len(drawdowns) * 100
    median_dd = _percentile(sorted_dd, 50) * 100
    p90_dd = _percentile(sorted_dd, 90) * 100

    # Annualised Sharpe (simplified: daily returns → annual).
    daily_returns = [r / mc.horizon_days for r in rois]
    dr_mean = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    dr_var = sum((d - dr_mean) ** 2 for d in daily_returns) / max(len(daily_returns) - 1, 1)
    dr_std = math.sqrt(dr_var)
    sharpe = (dr_mean / dr_std * math.sqrt(365)) if dr_std > 0 else 0.0

    return SimulationResult(
        num_simulations=mc.num_simulations,
        horizon_days=mc.horizon_days,
        mean_final_bankroll=round(mean_final, 2),
        median_final_bankroll=round(median_final, 2),
        p10_final_bankroll=round(p10_final, 2),
        p90_final_bankroll=round(p90_final, 2),
        min_final_bankroll=round(min(finals), 2),
        max_final_bankroll=round(max(finals), 2),
        mean_roi_pct=round(mean_roi, 2),
        median_roi_pct=round(median_roi, 2),
        p10_roi_pct=round(p10_roi, 2),
        prob_positive_roi=round(prob_pos, 4),
        prob_target_roi=round(prob_target, 4),
        mean_max_drawdown_pct=round(mean_dd, 2),
        median_max_drawdown_pct=round(median_dd, 2),
        p90_max_drawdown_pct=round(p90_dd, 2),
        estimated_sharpe=round(sharpe, 2),
        terminal_bankrolls=finals,
    )


def sweep_kelly_fractions(
    fractions: Sequence[float] = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.50),
    win_rate: float = 0.58,
    avg_edge: float = 0.08,
    config: BotConfig | None = None,
) -> List[SimulationResult]:
    """Run simulations across multiple Kelly fractions to find optimal."""
    cfg = config or DEFAULT_CONFIG
    results = []
    for frac in fractions:
        c = cfg.copy()
        c.kelly.base_fraction = frac
        results.append(run_simulation(win_rate=win_rate, avg_edge=avg_edge, config=c))
    return results
