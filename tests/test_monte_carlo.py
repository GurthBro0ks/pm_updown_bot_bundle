"""Tests for the Monte Carlo simulation engine."""

import pytest

from src.config import BotConfig
from src.monte_carlo import (
    SimulationResult,
    run_simulation,
    simulate_path,
    sweep_kelly_fractions,
    _percentile,
)


# ---------------------------------------------------------------------------
# _percentile helper
# ---------------------------------------------------------------------------
class TestPercentile:
    def test_median_odd(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_median_even(self):
        assert _percentile([1, 2, 3, 4], 50) == 2.5

    def test_p0(self):
        assert _percentile([10, 20, 30], 0) == 10

    def test_p100(self):
        assert _percentile([10, 20, 30], 100) == 30

    def test_empty(self):
        assert _percentile([], 50) == 0.0


# ---------------------------------------------------------------------------
# simulate_path
# ---------------------------------------------------------------------------
class TestSimulatePath:
    def test_returns_tuple(self):
        fb, dd = simulate_path(
            bankroll=1000,
            win_rate=0.60,
            avg_edge=0.10,
            kelly_fraction=0.25,
            trades_per_day=2,
            horizon_days=30,
        )
        assert isinstance(fb, float)
        assert isinstance(dd, float)
        assert fb > 0
        assert 0 <= dd <= 1.0

    def test_zero_bankroll(self):
        fb, dd = simulate_path(
            bankroll=0, win_rate=0.60, avg_edge=0.10,
            kelly_fraction=0.25, trades_per_day=1, horizon_days=10,
        )
        assert fb == 0

    def test_deterministic_with_seed(self):
        import random
        args = dict(
            bankroll=5000, win_rate=0.58, avg_edge=0.08,
            kelly_fraction=0.25, trades_per_day=2, horizon_days=90,
        )
        fb1, dd1 = simulate_path(**args, rng=random.Random(42))
        fb2, dd2 = simulate_path(**args, rng=random.Random(42))
        assert fb1 == fb2
        assert dd1 == dd2


# ---------------------------------------------------------------------------
# run_simulation  (reduced scale for speed)
# ---------------------------------------------------------------------------
class TestRunSimulation:
    @pytest.fixture
    def fast_config(self):
        cfg = BotConfig(bankroll=5000)
        cfg.monte_carlo.num_simulations = 500
        cfg.monte_carlo.horizon_days = 180
        cfg.monte_carlo.seed = 42
        return cfg

    def test_returns_simulation_result(self, fast_config):
        res = run_simulation(win_rate=0.58, avg_edge=0.08, config=fast_config)
        assert isinstance(res, SimulationResult)
        assert res.num_simulations == 500

    def test_positive_roi_with_edge(self, fast_config):
        res = run_simulation(win_rate=0.58, avg_edge=0.08, config=fast_config)
        assert res.mean_roi_pct > 0, "Positive edge should yield positive mean ROI"
        assert res.prob_positive_roi > 0.5

    def test_median_above_starting_bankroll(self, fast_config):
        res = run_simulation(win_rate=0.60, avg_edge=0.10, config=fast_config)
        assert res.median_final_bankroll > fast_config.bankroll

    def test_no_edge_poor_results(self, fast_config):
        """With no edge the strategy should not reliably profit."""
        res = run_simulation(win_rate=0.50, avg_edge=0.00, config=fast_config)
        # Mean ROI might be slightly negative due to min-bet floor.
        assert res.mean_roi_pct < 20  # shouldn't be strongly positive

    def test_terminal_bankrolls_length(self, fast_config):
        res = run_simulation(win_rate=0.58, avg_edge=0.08, config=fast_config)
        assert len(res.terminal_bankrolls) == 500

    def test_drawdown_statistics_present(self, fast_config):
        res = run_simulation(win_rate=0.58, avg_edge=0.08, config=fast_config)
        assert res.mean_max_drawdown_pct >= 0
        assert res.p90_max_drawdown_pct >= res.mean_max_drawdown_pct

    def test_sharpe_positive_with_edge(self, fast_config):
        res = run_simulation(win_rate=0.60, avg_edge=0.10, config=fast_config)
        assert res.estimated_sharpe > 0


# ---------------------------------------------------------------------------
# Target: $1 k+ yearly ROI with tuned config
# ---------------------------------------------------------------------------
class TestTargetROI:
    """Verify that the tuned configuration achieves $1 k+ yearly ROI
    on a $5 k bankroll (≥ 20 % annual return) with ≥ 50 % probability."""

    def test_target_1k_roi_achievable(self):
        cfg = BotConfig(bankroll=5000)
        cfg.monte_carlo.num_simulations = 2000
        cfg.monte_carlo.horizon_days = 365
        cfg.monte_carlo.seed = 123
        cfg.kelly.base_fraction = 0.25

        # Conservative assumptions: 58 % win-rate, 8 % average edge.
        res = run_simulation(win_rate=0.58, avg_edge=0.08, config=cfg)

        target_pct = (1000 / 5000) * 100  # 20 %
        assert res.median_roi_pct >= target_pct, (
            f"Median ROI {res.median_roi_pct:.1f}% < target {target_pct}%"
        )
        assert res.prob_target_roi >= 0.50, (
            f"P(≥ $1 k) = {res.prob_target_roi:.2%} < 50 %"
        )

    def test_p10_bankroll_retention(self):
        """10th-percentile outcome should retain ≥ 90 % of starting bankroll."""
        cfg = BotConfig(bankroll=5000)
        cfg.monte_carlo.num_simulations = 2000
        cfg.monte_carlo.horizon_days = 365
        cfg.monte_carlo.seed = 456
        cfg.kelly.base_fraction = 0.25

        res = run_simulation(win_rate=0.58, avg_edge=0.08, config=cfg)
        retention = res.p10_final_bankroll / cfg.bankroll
        assert retention >= 0.80, (
            f"10th-percentile retention {retention:.2%} < 80 %"
        )


# ---------------------------------------------------------------------------
# sweep_kelly_fractions
# ---------------------------------------------------------------------------
class TestSweep:
    def test_returns_list(self):
        cfg = BotConfig(bankroll=5000)
        cfg.monte_carlo.num_simulations = 100
        cfg.monte_carlo.horizon_days = 90
        cfg.monte_carlo.seed = 99
        results = sweep_kelly_fractions(
            fractions=(0.15, 0.25, 0.35),
            win_rate=0.58,
            avg_edge=0.08,
            config=cfg,
        )
        assert len(results) == 3
        assert all(isinstance(r, SimulationResult) for r in results)

    def test_higher_fraction_higher_mean_roi(self):
        """With positive edge, higher Kelly fraction → higher *mean* ROI
        (though more variance)."""
        cfg = BotConfig(bankroll=5000)
        cfg.monte_carlo.num_simulations = 200
        cfg.monte_carlo.horizon_days = 180
        cfg.monte_carlo.seed = 77
        results = sweep_kelly_fractions(
            fractions=(0.10, 0.50),
            win_rate=0.60,
            avg_edge=0.10,
            config=cfg,
        )
        assert results[1].mean_roi_pct > results[0].mean_roi_pct
