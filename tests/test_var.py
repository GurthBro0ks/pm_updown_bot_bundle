"""Tests for the Value-at-Risk module."""

import math
import pytest

from src.config import BotConfig
from src.var import (
    Position,
    VaRResult,
    check_daily_loss,
    check_drawdown,
    check_stop_loss,
    historical_var,
    parametric_var,
)


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------
class TestPosition:
    def test_cost_basis(self):
        p = Position("M1", "YES", 0.60, 100, 0.65)
        assert math.isclose(p.cost_basis, 60.0, abs_tol=1e-9)

    def test_market_value(self):
        p = Position("M1", "YES", 0.60, 100, 0.65)
        assert math.isclose(p.market_value, 65.0, abs_tol=1e-9)

    def test_unrealised_pnl(self):
        p = Position("M1", "YES", 0.60, 100, 0.65)
        assert math.isclose(p.unrealised_pnl, 5.0, abs_tol=1e-9)

    def test_max_loss_yes(self):
        p = Position("M1", "YES", 0.60, 100, 0.65)
        assert math.isclose(p.max_loss, 60.0, abs_tol=1e-9)

    def test_max_loss_no(self):
        p = Position("M1", "NO", 0.40, 100, 0.45)
        assert math.isclose(p.max_loss, 60.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# parametric_var
# ---------------------------------------------------------------------------
class TestParametricVaR:
    def test_empty_portfolio(self):
        res = parametric_var([], 5000)
        assert res.var_usd == 0.0
        assert res.num_positions == 0

    def test_single_position(self):
        pos = Position("M1", "YES", 0.55, 100, 0.55)
        res = parametric_var([pos], 5000)
        assert isinstance(res, VaRResult)
        assert res.var_usd >= 0
        assert res.total_exposure == pytest.approx(55.0, abs=0.01)
        assert res.num_positions == 1

    def test_multiple_positions(self):
        positions = [
            Position("M1", "YES", 0.55, 100, 0.55),
            Position("M2", "YES", 0.40, 50, 0.45),
            Position("M3", "NO", 0.60, 80, 0.55),
        ]
        res = parametric_var(positions, 5000)
        assert res.num_positions == 3
        assert res.total_exposure > 0

    def test_breach_concurrent_positions(self):
        cfg = BotConfig()
        cfg.var.max_concurrent_positions = 2
        positions = [
            Position("M1", "YES", 0.5, 10, 0.5),
            Position("M2", "YES", 0.5, 10, 0.5),
            Position("M3", "YES", 0.5, 10, 0.5),
        ]
        res = parametric_var(positions, 5000, cfg)
        assert any("concurrent_positions" in b for b in res.breaches)

    def test_breach_portfolio_risk(self):
        cfg = BotConfig(bankroll=100)
        cfg.var.max_portfolio_risk = 0.10  # 10 %
        # One position costing $20 out of $100 bankroll = 20 % exposure.
        positions = [Position("M1", "YES", 0.50, 40, 0.50)]
        res = parametric_var(positions, 100, cfg)
        assert any("portfolio_risk" in b for b in res.breaches)


# ---------------------------------------------------------------------------
# historical_var
# ---------------------------------------------------------------------------
class TestHistoricalVaR:
    def test_simple_series(self):
        returns = [-50, -30, -10, 0, 10, 20, 30, 40, 50, 60]
        var = historical_var(returns, confidence=0.95)
        # 5th percentile of 10 items → index 0 → −50 → VaR = 50.
        assert var == 50.0

    def test_all_positive(self):
        returns = [5, 10, 15, 20]
        var = historical_var(returns, confidence=0.95)
        assert var == 0.0

    def test_empty(self):
        assert historical_var([], 0.95) == 0.0


# ---------------------------------------------------------------------------
# Risk checks
# ---------------------------------------------------------------------------
class TestDailyLoss:
    def test_not_breached(self):
        assert check_daily_loss(-50, 5000) is False  # −1 %

    def test_breached(self):
        assert check_daily_loss(-150, 5000) is True  # −3 %

    def test_exactly_at_limit(self):
        assert check_daily_loss(-100, 5000) is True  # −2 % (≤ means halt)


class TestDrawdown:
    def test_no_drawdown(self):
        assert check_drawdown(5000, 5000) is False

    def test_breached(self):
        assert check_drawdown(4000, 5000) is True  # 20 %

    def test_just_under(self):
        assert check_drawdown(4300, 5000) is False  # 14 %


class TestStopLoss:
    def test_yes_stop_triggered(self):
        pos = Position("M1", "YES", 0.60, 100, 0.50)
        cfg = BotConfig()
        cfg.var.stop_loss_fraction = 0.05
        assert check_stop_loss(pos, cfg) is True  # 16.7 % loss

    def test_yes_stop_not_triggered(self):
        pos = Position("M1", "YES", 0.60, 100, 0.58)
        cfg = BotConfig()
        cfg.var.stop_loss_fraction = 0.05
        assert check_stop_loss(pos, cfg) is False  # 3.3 % loss

    def test_no_side_stop_triggered(self):
        pos = Position("M1", "NO", 0.40, 100, 0.55)
        cfg = BotConfig()
        cfg.var.stop_loss_fraction = 0.05
        # loss_frac = (0.55 - 0.40) / (1 - 0.40) = 0.25 → triggered
        assert check_stop_loss(pos, cfg) is True
