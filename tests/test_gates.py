import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/opt/slimy/pm_updown_bot_bundle")

from strategies.kalshi_optimize import check_micro_live_gates


def _base_risk_caps():
    return {
        "max_pos_usd": 10,
        "liquidity_min_usd": 0,
        "edge_after_fees_pct": 0.5,
        "market_end_hrs": 0,
    }


def _base_market(**overrides):
    m = {
        "ticker": "TEST-MARKET",
        "id": "test-id",
        "volume_24h": 100,
        "liquidity_usd": 100,
    }
    m.update(overrides)
    return m


# ── Gate 1: Minimum price floor ──────────────────────────────────────

class TestMinPriceFloor:
    def test_order_at_3c_rejected(self):
        market = _base_market()
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.03, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        assert any("minimum floor" in v for v in violations)

    def test_order_at_1c_rejected_with_message(self):
        market = _base_market(ticker="KXBTC-26DEC31-T50000")
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.01, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        floor_violations = [v for v in violations if "minimum floor" in v]
        assert len(floor_violations) == 1
        assert "KXBTC-26DEC31-T50000" in floor_violations[0]
        assert "1c" in floor_violations[0]
        assert "5c" in floor_violations[0]

    def test_order_at_5c_passes_floor(self):
        market = _base_market()
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.05, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        floor_violations = [v for v in violations if "minimum floor" in v]
        assert len(floor_violations) == 0

    def test_order_at_50c_no_false_positive(self):
        market = _base_market()
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.50, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        floor_violations = [v for v in violations if "minimum floor" in v]
        assert len(floor_violations) == 0

    def test_order_at_4c_rejected(self):
        market = _base_market()
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.04, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        assert any("minimum floor" in v for v in violations)

    def test_order_at_99c_no_false_positive(self):
        market = _base_market()
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.99, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        floor_violations = [v for v in violations if "minimum floor" in v]
        assert len(floor_violations) == 0

    def test_configurable_threshold(self):
        market = _base_market()
        with patch("config.MIN_PRICE_CENTS", 10):
            passed, violations = check_micro_live_gates(
                market, size=1.0, price=0.07, risk_caps=_base_risk_caps(), venue="kalshi"
            )
            assert not passed
            assert any("minimum floor" in v for v in violations)


# ── Gate 2: Long-dated prior cap ─────────────────────────────────────

class TestLongDatedPriorCap:
    def _apply_cap(self, true_price, yes_price, days_to_end):
        from config import LONG_DATED_EXPIRY_DAYS, LONG_DATED_PRIOR_CAP_ABOVE_MARKET
        capped = true_price
        if days_to_end is not None and days_to_end != float("inf"):
            if days_to_end > LONG_DATED_EXPIRY_DAYS:
                cap = yes_price + LONG_DATED_PRIOR_CAP_ABOVE_MARKET
                if capped > cap:
                    capped = cap
        return capped

    def test_90d_expiry_market_5c_prior_095_capped(self):
        result = self._apply_cap(true_price=0.95, yes_price=0.05, days_to_end=90)
        assert result == pytest.approx(0.20, abs=0.001)

    def test_90d_expiry_market_30c_prior_040_not_capped(self):
        result = self._apply_cap(true_price=0.40, yes_price=0.30, days_to_end=90)
        assert result == pytest.approx(0.40, abs=0.001)

    def test_5d_expiry_market_5c_prior_095_not_capped(self):
        result = self._apply_cap(true_price=0.95, yes_price=0.05, days_to_end=5)
        assert result == pytest.approx(0.95, abs=0.001)

    def test_90d_expiry_market_5c_prior_015_not_capped(self):
        result = self._apply_cap(true_price=0.15, yes_price=0.05, days_to_end=90)
        assert result == pytest.approx(0.15, abs=0.001)

    def test_missing_expiry_data_no_crash(self):
        result = self._apply_cap(true_price=0.95, yes_price=0.05, days_to_end=None)
        assert result == pytest.approx(0.95, abs=0.001)

    def test_inf_expiry_no_crash(self):
        result = self._apply_cap(true_price=0.95, yes_price=0.05, days_to_end=float("inf"))
        assert result == pytest.approx(0.95, abs=0.001)

    def test_exactly_30d_not_capped(self):
        result = self._apply_cap(true_price=0.95, yes_price=0.05, days_to_end=30)
        assert result == pytest.approx(0.95, abs=0.001)

    def test_31d_capped(self):
        result = self._apply_cap(true_price=0.95, yes_price=0.05, days_to_end=31)
        assert result == pytest.approx(0.20, abs=0.001)

    def test_180d_market_1c_prior_099_capped(self):
        result = self._apply_cap(true_price=0.99, yes_price=0.01, days_to_end=180)
        assert result == pytest.approx(0.16, abs=0.001)

    def test_90d_market_50c_prior_070_not_capped(self):
        result = self._apply_cap(true_price=0.70, yes_price=0.50, days_to_end=90)
        assert result == pytest.approx(0.65, abs=0.001)


# ── Combined gates ────────────────────────────────────────────────────

class TestCombinedGates:
    def test_2c_order_rejected_by_floor_before_cap(self):
        market = _base_market(_days_to_end=90, _ai_true_price=0.95)
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.02, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        assert any("minimum floor" in v for v in violations)

    def test_5c_order_on_5d_market_passes_floor(self):
        market = _base_market()
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.05, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        floor_violations = [v for v in violations if "minimum floor" in v]
        assert len(floor_violations) == 0

    def test_scratchpad_log_on_gate_rejection(self):
        scratchpad = MagicMock()
        scratchpad.log = MagicMock()
        market = _base_market(ticker="KXTEST-REJECT")
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.03, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        assert any("minimum floor" in v for v in violations)
