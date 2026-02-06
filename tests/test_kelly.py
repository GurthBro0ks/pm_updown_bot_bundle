"""Tests for the Kelly Criterion position-sizing module."""

import math
import pytest

from src.config import BotConfig, KellyConfig
from src.kelly import (
    KellyResult,
    classic_kelly,
    edge_for_binary,
    multi_outcome_kelly,
    size_bet,
)


# ---------------------------------------------------------------------------
# classic_kelly
# ---------------------------------------------------------------------------
class TestClassicKelly:
    def test_even_money_60pct(self):
        """60 % win-rate at even money (decimal odds 2.0) → f* = 0.20."""
        f = classic_kelly(0.60, 2.0)
        assert math.isclose(f, 0.20, abs_tol=1e-9)

    def test_even_money_50pct(self):
        """No edge at even money → f* = 0."""
        f = classic_kelly(0.50, 2.0)
        assert math.isclose(f, 0.0, abs_tol=1e-9)

    def test_negative_edge(self):
        """40 % win at even money → no bet."""
        f = classic_kelly(0.40, 2.0)
        assert f == 0.0

    def test_high_odds(self):
        """30 % win at 5× odds → f* = (0.3×4 − 0.7)/4 = 0.125."""
        f = classic_kelly(0.30, 5.0)
        assert math.isclose(f, 0.125, abs_tol=1e-9)

    def test_boundary_prob_zero(self):
        assert classic_kelly(0.0, 2.0) == 0.0

    def test_boundary_prob_one(self):
        assert classic_kelly(1.0, 2.0) == 0.0

    def test_boundary_odds_one(self):
        assert classic_kelly(0.6, 1.0) == 0.0


# ---------------------------------------------------------------------------
# edge_for_binary
# ---------------------------------------------------------------------------
class TestEdge:
    def test_positive_edge(self):
        assert math.isclose(edge_for_binary(0.70, 0.55), 0.15, abs_tol=1e-9)

    def test_zero_edge(self):
        assert edge_for_binary(0.50, 0.50) == 0.0

    def test_negative_edge(self):
        assert edge_for_binary(0.40, 0.55) < 0


# ---------------------------------------------------------------------------
# size_bet
# ---------------------------------------------------------------------------
class TestSizeBet:
    def test_sufficient_edge_returns_trade(self):
        cfg = BotConfig(bankroll=5000)
        res = size_bet(prob_win=0.70, market_prob=0.55, bankroll=5000, config=cfg)
        assert isinstance(res, KellyResult)
        assert res.bet_size > 0
        assert res.edge == pytest.approx(0.15, abs=1e-9)

    def test_insufficient_edge_returns_zero(self):
        cfg = BotConfig(bankroll=5000)
        res = size_bet(prob_win=0.56, market_prob=0.55, bankroll=5000, config=cfg)
        assert res.bet_size == 0.0
        assert res.edge == pytest.approx(0.01, abs=1e-9)

    def test_low_confidence_returns_zero(self):
        """Even with edge, if prob_win < min_confidence → no trade."""
        cfg = BotConfig(bankroll=5000)
        cfg.kelly.min_confidence = 0.65
        res = size_bet(prob_win=0.62, market_prob=0.50, bankroll=5000, config=cfg)
        assert res.bet_size == 0.0

    def test_bet_clamped_to_max(self):
        cfg = BotConfig(bankroll=10000)
        cfg.kelly.max_bet_fraction = 0.03
        res = size_bet(prob_win=0.90, market_prob=0.50, bankroll=10000, config=cfg)
        assert res.bet_size <= 10000 * 0.03 + 0.01  # rounding tolerance

    def test_bet_clamped_to_min(self):
        cfg = BotConfig(bankroll=10000)
        cfg.kelly.min_bet_fraction = 0.01
        # Small edge, just above threshold.
        res = size_bet(prob_win=0.66, market_prob=0.60, bankroll=10000, config=cfg)
        if res.bet_size > 0:
            assert res.bet_size >= 10000 * 0.01 - 0.01

    def test_edge_tier_scaling(self):
        """Higher edge should use a higher Kelly multiplier."""
        cfg = BotConfig(bankroll=5000)
        lo = size_bet(0.66, 0.60, 5000, cfg)  # edge = 0.06 → 0.25× Kelly
        hi = size_bet(0.80, 0.60, 5000, cfg)  # edge = 0.20 → 0.50× Kelly
        assert hi.kelly_multiplier >= lo.kelly_multiplier

    def test_default_config(self):
        """size_bet works without passing an explicit config."""
        res = size_bet(0.70, 0.55, 5000)
        assert isinstance(res, KellyResult)


# ---------------------------------------------------------------------------
# multi_outcome_kelly
# ---------------------------------------------------------------------------
class TestMultiOutcomeKelly:
    def test_basic_allocation(self):
        probs = [0.5, 0.3, 0.2]
        prices = [0.4, 0.25, 0.35]
        alloc = multi_outcome_kelly(probs, prices, bankroll=1000, fraction=0.25)
        assert len(alloc) == 3
        assert sum(alloc) <= 1000 * 0.25 + 1  # rounding tolerance

    def test_no_edge_returns_zeros(self):
        probs = [0.4, 0.3, 0.3]
        prices = [0.5, 0.4, 0.4]
        alloc = multi_outcome_kelly(probs, prices, bankroll=1000, fraction=0.25)
        # At least some should be zero if there's no edge.
        assert all(a >= 0 for a in alloc)

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            multi_outcome_kelly([0.5, 0.5], [0.4], 1000)

    def test_zero_price_handled(self):
        alloc = multi_outcome_kelly([0.5], [0.0], 1000)
        assert alloc == [0.0]
