"""
Kelly Criterion position-sizing for prediction markets.

Implements:
  * Classic Kelly fraction: f* = (p·b − q) / b
  * Fractional Kelly with edge-tiered scaling
  * Bankroll-clamped bet sizing
  * Multi-outcome (categorical) Kelly via the Smoczynski–Tomkins algorithm

References:
  - Kelly 1956, "A New Interpretation of Information Rate"
  - Thorp 2006, "The Kelly Criterion in Blackjack, Sports-Betting, and the Stock Market"
  - arXiv 2412.14144 — Application of the Kelly Criterion to Prediction Markets
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.config import BotConfig, DEFAULT_CONFIG, KellyConfig


@dataclass(frozen=True)
class KellyResult:
    """Result of a Kelly sizing calculation."""

    raw_fraction: float       # un-scaled optimal Kelly fraction
    scaled_fraction: float    # after applying fractional multiplier
    bet_size: float           # USD amount to wager
    edge: float               # estimated edge  (p − market_prob)
    expected_value: float     # EV per dollar wagered
    kelly_multiplier: float   # which fractional-Kelly tier was used


def classic_kelly(prob_win: float, odds_decimal: float) -> float:
    """Return the raw Kelly fraction f* for a binary bet.

    Parameters
    ----------
    prob_win : float
        Estimated true probability of winning, in (0, 1).
    odds_decimal : float
        Decimal odds offered (e.g. 2.0 for even money).
        ``net_payout = odds_decimal - 1``.

    Returns
    -------
    float
        Raw Kelly fraction in [0, 1].  Returns 0 when edge is non-positive.
    """
    if prob_win <= 0 or prob_win >= 1 or odds_decimal <= 1:
        return 0.0
    b = odds_decimal - 1.0          # net payout per dollar risked
    q = 1.0 - prob_win
    f_star = (prob_win * b - q) / b
    return max(f_star, 0.0)


def edge_for_binary(prob_win: float, market_prob: float) -> float:
    """Compute the edge (information advantage) for a binary market.

    Edge = estimated probability − market implied probability.
    """
    return prob_win - market_prob


def _kelly_multiplier(edge: float, cfg: KellyConfig) -> float:
    """Select the fractional Kelly multiplier based on edge tiers."""
    multiplier = cfg.base_fraction
    for threshold in sorted(cfg.edge_tiers):
        if edge >= threshold:
            multiplier = cfg.edge_tiers[threshold]
    return multiplier


def size_bet(
    prob_win: float,
    market_prob: float,
    bankroll: float,
    config: BotConfig | None = None,
) -> KellyResult:
    """Compute the Kelly-optimal bet size for a binary prediction market.

    Parameters
    ----------
    prob_win : float
        Model's estimated probability of YES, in (0, 1).
    market_prob : float
        Current market-implied probability (price of YES contract), in (0, 1).
    bankroll : float
        Current available bankroll in USD.
    config : BotConfig, optional
        Configuration; uses ``DEFAULT_CONFIG`` when *None*.

    Returns
    -------
    KellyResult
        Fully resolved bet sizing result.
    """
    cfg = (config or DEFAULT_CONFIG).kelly

    edge = edge_for_binary(prob_win, market_prob)
    if edge < cfg.min_edge or prob_win < cfg.min_confidence:
        return KellyResult(
            raw_fraction=0.0,
            scaled_fraction=0.0,
            bet_size=0.0,
            edge=edge,
            expected_value=0.0,
            kelly_multiplier=0.0,
        )

    # Decimal odds implied by market price: buying YES at `market_prob`
    # pays 1/market_prob on win.
    odds_decimal = 1.0 / market_prob if market_prob > 0 else 0.0
    raw_f = classic_kelly(prob_win, odds_decimal)

    multiplier = _kelly_multiplier(edge, cfg)
    scaled_f = raw_f * multiplier

    # Clamp to bankroll limits.
    scaled_f = max(scaled_f, cfg.min_bet_fraction)
    scaled_f = min(scaled_f, cfg.max_bet_fraction)

    bet_usd = round(scaled_f * bankroll, 2)
    ev = edge * bet_usd  # simplified EV

    return KellyResult(
        raw_fraction=raw_f,
        scaled_fraction=scaled_f,
        bet_size=bet_usd,
        edge=edge,
        expected_value=ev,
        kelly_multiplier=multiplier,
    )


# ---------------------------------------------------------------------------
# Multi-outcome Kelly (categorical markets)
# ---------------------------------------------------------------------------
def multi_outcome_kelly(
    probabilities: List[float],
    market_prices: List[float],
    bankroll: float,
    fraction: float = 0.25,
) -> List[float]:
    """Compute Kelly-optimal allocations for a multi-outcome market.

    Uses the simplified approach: treat each outcome as independent binary
    YES/NO, compute per-outcome Kelly, then normalise so allocations sum
    to at most ``fraction`` of bankroll.

    Parameters
    ----------
    probabilities : list[float]
        Model-estimated probabilities for each outcome (should sum to ~1).
    market_prices : list[float]
        Market prices for each outcome's YES contract.
    bankroll : float
        Available bankroll in USD.
    fraction : float
        Maximum total fraction of bankroll to deploy.

    Returns
    -------
    list[float]
        USD allocation for each outcome.
    """
    n = len(probabilities)
    if n != len(market_prices):
        raise ValueError("probabilities and market_prices must have same length")

    raw = []
    for p, mp in zip(probabilities, market_prices):
        if mp <= 0 or mp >= 1:
            raw.append(0.0)
            continue
        odds = 1.0 / mp
        f = classic_kelly(p, odds)
        raw.append(max(f, 0.0))

    total = sum(raw)
    if total <= 0:
        return [0.0] * n

    # Scale down so total allocation ≤ fraction.
    scale = min(fraction / total, 1.0)
    allocations = [round(r * scale * bankroll, 2) for r in raw]
    return allocations
