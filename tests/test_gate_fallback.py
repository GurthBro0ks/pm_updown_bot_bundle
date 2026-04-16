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


class TestFallbackPriorGate:
    def test_fallback_prior_at_15c_rejected(self):
        market = _base_market(_ai_prior_is_fallback=True)
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.15, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        assert any("fallback prior" in v for v in violations)

    def test_real_prior_050_gemini_not_rejected(self):
        market = _base_market(_ai_prior_is_fallback=False, _ai_prior_source="gemini")
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.15, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        fallback_violations = [v for v in violations if "fallback prior" in v]
        assert len(fallback_violations) == 0

    def test_real_prior_060_grok420_not_rejected(self):
        market = _base_market(_ai_prior_is_fallback=False, _ai_prior_source="grok_420")
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.15, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        fallback_violations = [v for v in violations if "fallback prior" in v]
        assert len(fallback_violations) == 0

    def test_fallback_prior_short_dated_still_rejected(self):
        market = _base_market(_ai_prior_is_fallback=True, _days_to_end=0.5)
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.15, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        assert any("fallback prior" in v for v in violations)

    def test_fallback_prior_skip_disabled_not_rejected(self):
        market = _base_market(_ai_prior_is_fallback=True)
        with patch("config.SKIP_FALLBACK_PRIORS", False):
            passed, violations = check_micro_live_gates(
                market, size=1.0, price=0.15, risk_caps=_base_risk_caps(), venue="kalshi"
            )
            fallback_violations = [v for v in violations if "fallback prior" in v]
            assert len(fallback_violations) == 0

    def test_no_fallback_key_no_rejection(self):
        market = _base_market()
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.15, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        fallback_violations = [v for v in violations if "fallback prior" in v]
        assert len(fallback_violations) == 0

    def test_fallback_includes_ticker_in_message(self):
        market = _base_market(ticker="KXINX-26APR17-T5000", _ai_prior_is_fallback=True)
        passed, violations = check_micro_live_gates(
            market, size=1.0, price=0.15, risk_caps=_base_risk_caps(), venue="kalshi"
        )
        assert not passed
        fb = [v for v in violations if "fallback prior" in v]
        assert len(fb) == 1
        assert "KXINX-26APR17-T5000" in fb[0]
        assert "cascade failed" in fb[0]


class TestWasLastPriorFallback:
    def test_fallback_flag_set_on_fallback(self):
        from strategies.sentiment_scorer import was_last_prior_fallback, _last_prior_was_fallback
        import strategies.sentiment_scorer as ss
        ss._last_prior_was_fallback = True
        assert was_last_prior_fallback() is True

    def test_fallback_flag_cleared_on_success(self):
        import strategies.sentiment_scorer as ss
        ss._last_prior_was_fallback = False
        assert ss.was_last_prior_fallback() is False
