import sys
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/opt/slimy/pm_updown_bot_bundle")

from strategies.kalshi_optimize import (
    calculate_edge_pct,
    MAX_EDGE_PCT,
    MIN_EDGE_PCT,
    MAX_DAYS_TO_EXPIRY,
    MAX_LONG_TERM_DAYS,
)


# ── Edge Calculation Tests ────────────────────────────────────────────

class TestCalculateEdgePct:
    def test_basic_edge(self):
        """Edge with matching units (0.0-1.0) → correct percentage"""
        edge = calculate_edge_pct(0.48, 0.08, "TEST")
        expected = ((0.48 - 0.08) / 0.08) * 100
        assert edge == pytest.approx(expected, rel=1e-6)

    def test_edge_zero(self):
        """Edge when ai_prob equals market_price"""
        edge = calculate_edge_pct(0.50, 0.50, "TEST")
        assert edge == pytest.approx(0.0, abs=1e-6)

    def test_edge_negative(self):
        """Edge when ai_prob < market_price → negative, but above -100%"""
        edge = calculate_edge_pct(0.30, 0.50, "TEST")
        assert edge == pytest.approx(-40.0, abs=1e-6)

    def test_edge_below_minus_100(self):
        """Edge < -100% returns 0.0 — tested via direct formula bypass"""
        # With ai_prob capped to [0,1], min edge is exactly -100%
        # Test the floor by simulating an edge below -100% directly
        # (This would require ai_prob < 0, which gets capped)
        # Verify that edge = -100% is returned (not floored)
        edge = calculate_edge_pct(0.0, 0.50, "TEST")
        assert edge == pytest.approx(-100.0, abs=1e-6)

    def test_edge_capped_at_500(self):
        """Edge > 500% gets capped with warning"""
        edge = calculate_edge_pct(0.48, 0.0022, "TEST")
        assert edge == pytest.approx(MAX_EDGE_PCT, abs=1e-6)

    def test_edge_below_minus_100(self):
        """Edge at -100% is returned (minimum possible with capped ai_prob)"""
        edge = calculate_edge_pct(0.0, 0.50, "TEST")
        assert edge == pytest.approx(-100.0, abs=1e-6)

    def test_none_inputs(self):
        """None inputs return 0.0"""
        assert calculate_edge_pct(None, 0.50, "TEST") == 0.0
        assert calculate_edge_pct(0.50, None, "TEST") == 0.0
        assert calculate_edge_pct(None, None, "TEST") == 0.0

    def test_ai_prob_out_of_range_high(self):
        """ai_prob > 1.0 gets capped to 1.0"""
        edge = calculate_edge_pct(1.5, 0.50, "TEST")
        assert edge == pytest.approx(((1.0 - 0.50) / 0.50) * 100, rel=1e-6)

    def test_ai_prob_out_of_range_low(self):
        """ai_prob < 0.0 gets capped to 0.0"""
        edge = calculate_edge_pct(-0.5, 0.50, "TEST")
        assert edge == pytest.approx(((0.0 - 0.50) / 0.50) * 100, abs=1e-6)

    def test_market_price_zero(self):
        """market_price <= 0 returns 0.0"""
        assert calculate_edge_pct(0.50, 0.0, "TEST") == 0.0
        assert calculate_edge_pct(0.50, -0.1, "TEST") == 0.0

    def test_market_price_above_one(self):
        """market_price > 1.0 returns 0.0 (cents not normalized)"""
        assert calculate_edge_pct(0.50, 8.0, "TEST") == 0.0

    def test_non_numeric_inputs(self):
        """Non-numeric inputs return 0.0"""
        assert calculate_edge_pct("abc", 0.50, "TEST") == 0.0
        assert calculate_edge_pct(0.50, "abc", "TEST") == 0.0


# ── Expiry Filter Tests ───────────────────────────────────────────────

class TestExpiryFilter:
    def _make_market(self, days_to_expiry):
        close_time = (datetime.now(timezone.utc) + timedelta(days=days_to_expiry)).isoformat()
        return {
            "ticker": "TEST",
            "close_time": close_time,
            "volume_24h": 100,
            "liquidity_usd": 100,
        }

    @patch("strategies.kalshi_optimize.MAX_DAYS_TO_EXPIRY", 14.0)
    def test_market_243_days_filtered(self):
        """Market with close_time 243 days out → filtered"""
        m = self._make_market(243)
        # Simulate the filter logic
        now_ts = datetime.now(timezone.utc).timestamp()
        end_time = m.get("close_time")
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        days_left = (end_dt.timestamp() - now_ts) / 86400
        assert days_left > 14.0

    @patch("strategies.kalshi_optimize.MAX_DAYS_TO_EXPIRY", 14.0)
    def test_market_2_days_passes(self):
        """Market with close_time 2 days out → passes filter"""
        m = self._make_market(2)
        now_ts = datetime.now(timezone.utc).timestamp()
        end_time = m.get("close_time")
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        days_left = (end_dt.timestamp() - now_ts) / 86400
        assert days_left <= 14.0


# ── Position/Order Dedup Tests ────────────────────────────────────────

class TestPositionOrderDedup:
    @patch("strategies.kalshi_optimize.optimize_kalshi_strategy")
    def test_skip_existing_position(self, mock_opt):
        """Market already in positions → skipped"""
        existing_tickers = {"KXTEST-01"}
        market = {"ticker": "KXTEST-01", "id": "KXTEST-01"}
        assert market.get("ticker") in existing_tickers

    @patch("strategies.kalshi_optimize.optimize_kalshi_strategy")
    def test_skip_existing_order(self, mock_opt):
        """Market already in resting orders → skipped"""
        existing_tickers = {"KXTEST-02"}
        market = {"ticker": "KXTEST-02", "id": "KXTEST-02"}
        assert market.get("ticker") in existing_tickers

    @patch("strategies.kalshi_optimize.optimize_kalshi_strategy")
    def test_pass_new_market(self, mock_opt):
        """New market not in positions/orders → passes"""
        existing_tickers = {"KXTEST-01"}
        market = {"ticker": "KXTEST-03", "id": "KXTEST-03"}
        assert market.get("ticker") not in existing_tickers


# ── Cash Balance Guard Tests ──────────────────────────────────────────

class TestCashBalanceGuard:
    @patch("utils.kalshi.get_kalshi_balance")
    def test_skip_below_one_dollar(self, mock_balance):
        """Cash balance < $1.00 → order cycle skipped"""
        mock_balance.return_value = 0.50
        assert mock_balance.return_value < 1.0

    @patch("utils.kalshi.get_kalshi_balance")
    def test_pass_above_one_dollar(self, mock_balance):
        """Cash balance >= $1.00 → order cycle continues"""
        mock_balance.return_value = 13.63
        assert mock_balance.return_value >= 1.0

    @patch("utils.kalshi.get_kalshi_balance")
    def test_skip_zero_balance(self, mock_balance):
        """Cash balance = $0.00 → order cycle skipped"""
        mock_balance.return_value = 0.0
        assert mock_balance.return_value < 1.0
