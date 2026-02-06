"""Tests for the Risk Manager orchestrator."""

import pytest

from src.config import BotConfig
from src.risk_manager import RiskManager, TradeDecision
from src.var import Position


@pytest.fixture
def rm():
    cfg = BotConfig(bankroll=5000)
    cfg.monte_carlo.num_simulations = 200
    cfg.monte_carlo.seed = 42
    return RiskManager(cfg)


# ---------------------------------------------------------------------------
# evaluate_trade
# ---------------------------------------------------------------------------
class TestEvaluateTrade:
    def test_trade_with_edge(self, rm):
        d = rm.evaluate_trade(prob_win=0.70, market_prob=0.55)
        assert d.action == "TRADE"
        assert d.kelly.bet_size > 0

    def test_skip_no_edge(self, rm):
        d = rm.evaluate_trade(prob_win=0.56, market_prob=0.55)
        assert d.action == "SKIP"
        assert "insufficient edge" in d.reason

    def test_halt_daily_loss(self, rm):
        rm.daily_pnl = -200  # −4 % of $5 k → exceeds 2 % limit
        d = rm.evaluate_trade(prob_win=0.70, market_prob=0.55)
        assert d.action == "HALT"
        assert d.daily_loss_halt is True

    def test_halt_drawdown(self, rm):
        rm.peak_equity = 5000
        rm.bankroll = 4000  # 20 % drawdown → exceeds 15 % limit
        d = rm.evaluate_trade(prob_win=0.70, market_prob=0.55)
        assert d.action == "HALT"
        assert d.drawdown_halt is True

    def test_skip_on_var_breach(self, rm):
        # Fill up to max concurrent positions.
        for i in range(10):
            rm.open_position(Position(f"M{i}", "YES", 0.50, 10, 0.50))
        d = rm.evaluate_trade(prob_win=0.70, market_prob=0.55, market_id="NEW")
        assert d.action == "SKIP"
        assert "concurrent_positions" in d.reason


# ---------------------------------------------------------------------------
# Portfolio management
# ---------------------------------------------------------------------------
class TestPortfolio:
    def test_open_and_close_position_win(self, rm):
        pos = Position("M1", "YES", 0.50, 100, 0.50)
        rm.open_position(pos)
        assert rm.num_open_positions == 1

        pnl = rm.close_position("M1", settlement_price=1.0)
        assert pnl == pytest.approx(50.0, abs=0.01)
        assert rm.bankroll > 5000
        assert rm.num_open_positions == 0

    def test_close_position_loss(self, rm):
        pos = Position("M1", "YES", 0.60, 100, 0.60)
        rm.open_position(pos)
        pnl = rm.close_position("M1", settlement_price=0.0)
        assert pnl == pytest.approx(-60.0, abs=0.01)
        assert rm.bankroll < 5000

    def test_close_nonexistent(self, rm):
        pnl = rm.close_position("NOPE", 1.0)
        assert pnl == 0.0

    def test_daily_pnl_tracking(self, rm):
        pos = Position("M1", "YES", 0.50, 100, 0.50)
        rm.open_position(pos)
        rm.close_position("M1", 1.0)
        assert rm.daily_pnl == pytest.approx(50.0, abs=0.01)
        rm.reset_daily_pnl()
        assert rm.daily_pnl == 0.0


# ---------------------------------------------------------------------------
# Stop-loss detection
# ---------------------------------------------------------------------------
class TestStopLossDetection:
    def test_positions_needing_stop_loss(self, rm):
        # One underwater, one fine.
        rm.open_position(Position("M1", "YES", 0.60, 100, 0.40))  # big loss
        rm.open_position(Position("M2", "YES", 0.50, 100, 0.49))  # small loss
        stops = rm.positions_needing_stop_loss()
        assert any(p.market_id == "M1" for p in stops)


# ---------------------------------------------------------------------------
# Monte Carlo validation
# ---------------------------------------------------------------------------
class TestMCValidation:
    def test_validate_strategy(self, rm):
        res = rm.validate_strategy(win_rate=0.58, avg_edge=0.08)
        assert res is not None
        assert res.num_simulations == 200
        assert rm.mc_result is res

    def test_validate_positive_edge(self, rm):
        res = rm.validate_strategy(win_rate=0.60, avg_edge=0.10)
        assert res.mean_roi_pct > 0


# ---------------------------------------------------------------------------
# Portfolio queries
# ---------------------------------------------------------------------------
class TestQueries:
    def test_total_exposure(self, rm):
        rm.open_position(Position("M1", "YES", 0.50, 100, 0.50))
        rm.open_position(Position("M2", "YES", 0.40, 50, 0.45))
        assert rm.total_exposure == pytest.approx(70.0, abs=0.01)

    def test_portfolio_risk_pct(self, rm):
        rm.open_position(Position("M1", "YES", 0.50, 200, 0.50))
        # exposure = 100, bankroll = 5000 → 2 %
        assert rm.portfolio_risk_pct == pytest.approx(0.02, abs=0.01)
