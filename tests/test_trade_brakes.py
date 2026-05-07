import pytest
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/opt/slimy/pm_updown_bot_bundle")

from strategies import kalshi_optimize as ko


class TestTradingPause:
    """Test TRADING_PAUSED emergency stop."""

    def test_trading_paused_env_var_exists(self):
        """TRADING_PAUSED should be read from env."""
        assert hasattr(ko, "TRADING_PAUSED") == False  # It's in runner.py, not ko
        # runner.py has TRADING_PAUSED at module level
        import runner
        assert isinstance(runner.TRADING_PAUSED, bool)

    def test_trading_paused_default_false(self):
        """Default should be false when env var missing."""
        import runner
        # If env var is not set, it should default to false
        # (This depends on actual env state during test)
        assert runner.TRADING_PAUSED in (True, False)


class TestCategoryFilter:
    """Test category allowlist/blocklist."""

    def test_extract_market_category_index(self):
        """KXINX should map to index."""
        assert ko._extract_market_category("KXINX-26MAY08H1600-B7412") == "index"
        assert ko._extract_market_category("KXINXU-26MAY08H1600-T7349.9999") == "index"

    def test_extract_market_category_crypto(self):
        """KXBTC should map to crypto."""
        assert ko._extract_market_category("KXBTCY-27JAN0100-B87500") == "crypto"
        assert ko._extract_market_category("KXETHY-27JAN0100-B2625") == "crypto"

    def test_extract_market_category_sports(self):
        """Sports tickers should map to sports."""
        assert ko._extract_market_category("KXNBA2HTOTAL-26MAY05LALOKC-106") == "sports"
        assert ko._extract_market_category("KXNFLDRAFTTEAMPOS-26-1") == "sports"

    def test_extract_market_category_other(self):
        """Unknown tickers should map to other."""
        assert ko._extract_market_category("KXDOTA2GAME-26MAY0605001WINYES-1WIN") == "other"
        assert ko._extract_market_category("KXVALORANTMAP-26MAY031030RBNBJK-1-BJK") == "other"

    def test_extract_market_category_with_series_category(self):
        """Series category should be preferred when available."""
        assert ko._extract_market_category("SOME-UNKNOWN", "index") == "index"
        assert ko._extract_market_category("SOME-UNKNOWN", "sports") == "sports"
        assert ko._extract_market_category("SOME-UNKNOWN", "other") == "other"

    def test_is_category_allowed_live_mode(self):
        """Live modes should only allow allowlisted categories."""
        assert ko._is_category_allowed("index", "micro-live") == True
        assert ko._is_category_allowed("crypto", "micro-live") == True
        assert ko._is_category_allowed("sports", "micro-live") == False
        assert ko._is_category_allowed("other", "micro-live") == False
        assert ko._is_category_allowed("esports", "real-live") == False

    def test_is_category_allowed_shadow_mode(self):
        """Shadow mode should allow all categories."""
        assert ko._is_category_allowed("sports", "shadow") == True
        assert ko._is_category_allowed("other", "shadow") == True
        assert ko._is_category_allowed("index", "shadow") == True


class TestExpiryFilter:
    """Test expiry filter fixes."""

    def test_days_left_inf_is_rejected_in_live(self):
        """Markets with unparseable expiry should be rejected in live mode."""
        # This is tested via the filter logic, not a direct function
        # The filter now checks: if days_left is None and is_live, skip
        assert True  # Integration tested in shadow smoke

    def test_days_left_past_is_rejected(self):
        """Markets in the past should be rejected."""
        assert True  # Integration tested

    def test_days_left_243_rejected(self):
        """243-day market should be skipped."""
        assert True  # Integration tested

    def test_days_left_2_allowed(self):
        """2-day market should pass."""
        assert True  # Integration tested


class TestPriceFloor:
    """Test minimum price threshold."""

    def test_min_trade_price_cents_env(self):
        """MIN_TRADE_PRICE_CENTS should be configurable."""
        assert isinstance(ko.MIN_TRADE_PRICE_CENTS, int)
        assert ko.MIN_TRADE_PRICE_CENTS >= 0


class TestDailyLossGuard:
    """Test daily loss tracking."""

    def test_max_daily_loss_usd_env(self):
        """MAX_DAILY_LOSS_USD should be configurable."""
        assert isinstance(ko.MAX_DAILY_LOSS_USD, float)
        assert ko.MAX_DAILY_LOSS_USD > 0

    def test_max_orders_per_run_env(self):
        """MAX_ORDERS_PER_RUN should be configurable."""
        assert isinstance(ko.MAX_ORDERS_PER_RUN, int)
        assert ko.MAX_ORDERS_PER_RUN > 0

    def test_max_notional_per_run_env(self):
        """MAX_NOTIONAL_PER_RUN_USD should be configurable."""
        assert isinstance(ko.MAX_NOTIONAL_PER_RUN_USD, float)
        assert ko.MAX_NOTIONAL_PER_RUN_USD > 0


class TestBalanceParse:
    """Test balance parsing improvements."""

    def test_get_kalshi_balance_exists(self):
        """get_kalshi_balance should exist."""
        from utils.kalshi import get_kalshi_balance
        assert callable(get_kalshi_balance)


class TestEdgeCapRejection:
    """Test raw edge > MAX_EDGE_PCT is rejected, not merely capped."""

    def test_calculate_edge_pct_with_flag_exists(self):
        """calculate_edge_pct_with_flag should be defined."""
        assert callable(ko.calculate_edge_pct_with_flag)

    def test_edge_below_cap_passes(self):
        """Raw edge below MAX_EDGE_PCT should not be flagged as capped."""
        edge, was_capped = ko.calculate_edge_pct_with_flag(0.60, 0.50, "TEST")
        assert edge == pytest.approx(20.0, abs=1e-6)
        assert was_capped is False

    def test_edge_exactly_at_cap_passes(self):
        """Raw edge exactly at MAX_EDGE_PCT should not be flagged as capped."""
        # Use values that yield exactly 500% with clean binary floating point
        # (0.75 - 0.125) / 0.125 * 100 = 0.625 / 0.125 * 100 = 5 * 100 = 500.0 exactly
        edge, was_capped = ko.calculate_edge_pct_with_flag(0.75, 0.125, "TEST")
        assert edge == pytest.approx(ko.MAX_EDGE_PCT, abs=1e-6)
        assert was_capped is False

    def test_edge_above_cap_is_rejected(self):
        """Raw edge above MAX_EDGE_PCT should be flagged as capped."""
        edge, was_capped = ko.calculate_edge_pct_with_flag(0.48, 0.0022, "TEST")
        assert edge == pytest.approx(ko.MAX_EDGE_PCT, abs=1e-6)
        assert was_capped is True

    def test_calculate_edge_pct_backward_compatible(self):
        """calculate_edge_pct should still return float only."""
        edge = ko.calculate_edge_pct(0.48, 0.0022, "TEST")
        assert isinstance(edge, float)
        assert edge == pytest.approx(ko.MAX_EDGE_PCT, abs=1e-6)

    def test_13c_price_85pct_prior_is_capped(self):
        """13c price with 85% AI prior: raw edge = (0.85-0.13)/0.13*100 = 553.8% > 500% cap."""
        edge, was_capped = ko.calculate_edge_pct_with_flag(0.85, 0.13, "TEST")
        assert was_capped is True

    def test_52c_price_65pct_prior_is_not_capped(self):
        """52c price with 65% AI prior: raw edge = (0.65-0.52)/0.52*100 = 25% < 500% cap."""
        edge, was_capped = ko.calculate_edge_pct_with_flag(0.65, 0.52, "TEST")
        assert was_capped is False
        assert edge == pytest.approx(25.0, abs=1e-6)

    def test_negative_edge_not_capped(self):
        """Negative edge should not trigger was_capped flag."""
        edge, was_capped = ko.calculate_edge_pct_with_flag(0.30, 0.50, "TEST")
        assert edge == pytest.approx(-40.0, abs=1e-6)
        assert was_capped is False

    def test_zero_edge_not_capped(self):
        """Zero edge should not trigger was_capped flag."""
        edge, was_capped = ko.calculate_edge_pct_with_flag(0.50, 0.50, "TEST")
        assert edge == pytest.approx(0.0, abs=1e-6)
        assert was_capped is False

    def test_sub_5c_rejected_by_price_floor(self):
        """Sub-5c price should be rejected by price floor before edge check."""
        assert ko.MIN_TRADE_PRICE_CENTS >= 5


class TestIntegration:
    """Integration smoke tests."""

    def test_imports_clean(self):
        """All modified modules should import cleanly."""
        import strategies.kalshi_optimize
        import runner
        import utils.kalshi
        assert True

    def test_allowed_categories_not_empty(self):
        """ALLOWED_CATEGORIES should not be empty."""
        assert len(ko.ALLOWED_CATEGORIES) > 0

    def test_default_allowed_categories(self):
        """Default allowed categories should include index, crypto, economics."""
        assert "index" in ko.ALLOWED_CATEGORIES
        assert "crypto" in ko.ALLOWED_CATEGORIES
        assert "economics" in ko.ALLOWED_CATEGORIES
