"""
Tests for utils/kalshi_normalize.py

Covers the March 2026 Kalshi API field migration:
- Modern dollar/fixed-point string fields
- Legacy integer-cent fallback
- Missing vs present-zero distinction
- Decimal parsing safety
"""

import pytest
from decimal import Decimal
from utils.kalshi_normalize import normalize_kalshi_market, _parse_decimal_string, _field_present


class TestParseDecimalString:
    def test_parses_string_dollar(self):
        assert _parse_decimal_string("0.1200") == Decimal("0.1200")

    def test_parses_string_fp(self):
        assert _parse_decimal_string("10.00") == Decimal("10.00")

    def test_parses_int(self):
        assert _parse_decimal_string(37) == Decimal("37")

    def test_parses_float(self):
        assert _parse_decimal_string(0.12) == Decimal("0.12")

    def test_none_returns_none(self):
        assert _parse_decimal_string(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_decimal_string("") is None

    def test_whitespace_string_returns_none(self):
        assert _parse_decimal_string("   ") is None

    def test_zero_string_returns_decimal_zero(self):
        dec = _parse_decimal_string("0.0000")
        assert dec is not None
        assert dec == Decimal("0.0000")


class TestFieldPresent:
    def test_key_exists_not_none(self):
        assert _field_present({"yes_bid_dollars": "0.1200"}, "yes_bid_dollars") is True

    def test_key_exists_zero_string(self):
        assert _field_present({"yes_bid_dollars": "0.0000"}, "yes_bid_dollars") is True

    def test_key_exists_zero_int(self):
        assert _field_present({"yes_bid": 0}, "yes_bid") is True

    def test_key_missing(self):
        assert _field_present({}, "yes_bid_dollars") is False

    def test_value_none(self):
        assert _field_present({"yes_bid_dollars": None}, "yes_bid_dollars") is False


class TestModernApiResponse:
    """Test case 1: Modern API response (all *_dollars and *_fp fields present, as strings)"""

    def test_full_modern_response(self):
        raw = {
            "ticker": "KXETHY-26DEC31-B2000",
            "short_name": "ETH above 2000",
            "yes_bid_dollars": "0.1200",
            "yes_ask_dollars": "0.1500",
            "no_bid_dollars": "0.8500",
            "no_ask_dollars": "0.8800",
            "last_price_dollars": "0.1350",
            "volume_fp": "10000.00",
            "volume_24h_fp": "2500.50",
            "open_interest_fp": "5000.00",
            "fee_multiplier": "0.07",
            "close_time": "2026-12-31T23:59:59Z",
        }
        result = normalize_kalshi_market(raw)

        assert result["id"] == "KXETHY-26DEC31-B2000"
        assert result["ticker"] == "KXETHY-26DEC31-B2000"
        assert result["question"] == "ETH above 2000"

        # Prices
        assert result["yes_bid"] == pytest.approx(0.12)
        assert result["yes_ask"] == pytest.approx(0.15)
        assert result["no_bid"] == pytest.approx(0.85)
        assert result["no_ask"] == pytest.approx(0.88)

        # Volume / OI
        assert result["volume"] == pytest.approx(10000.00)
        assert result["volume_24h"] == pytest.approx(2500.50)
        assert result["open_interest"] == pytest.approx(5000.00)

        # Odds
        assert result["odds"]["yes"] == pytest.approx(0.135)  # midpoint
        assert result["odds"]["no"] == pytest.approx(0.865)

        # Liquidity from reported
        assert result["liquidity_usd"] == pytest.approx(5000.0 * 0.135)

        # Source tracking
        assert result["raw_field_source"]["yes_bid"] == "yes_bid_dollars"
        assert result["raw_field_source"]["yes_ask"] == "yes_ask_dollars"
        assert result["raw_field_source"]["volume"] == "volume_fp"
        assert result["raw_field_source"]["open_interest"] == "open_interest_fp"

    def test_no_divide_by_100_on_dollar_fields(self):
        raw = {
            "ticker": "TEST",
            "yes_bid_dollars": "0.5000",
            "yes_ask_dollars": "0.5000",
        }
        result = normalize_kalshi_market(raw)
        assert result["yes_bid"] == pytest.approx(0.50)
        assert result["yes_ask"] == pytest.approx(0.50)
        assert result["raw_field_source"]["yes_bid"] == "yes_bid_dollars"


class TestLegacyApiResponse:
    """Test case 2: Legacy API response (integer cent fields only, no *_dollars)"""

    def test_full_legacy_cents_response(self):
        raw = {
            "ticker": "KXETHY-26DEC31-B2000",
            "title": "ETH above 2000",
            "yes_bid": 12,
            "yes_ask": 15,
            "no_bid": 85,
            "no_ask": 88,
            "last_price": 13,
            "volume": 10000,
            "volume_24h": 2500,
            "open_interest": 5000,
            "fee_multiplier": 0.07,
        }
        result = normalize_kalshi_market(raw)

        assert result["yes_bid"] == pytest.approx(0.12)
        assert result["yes_ask"] == pytest.approx(0.15)
        assert result["no_bid"] == pytest.approx(0.85)
        assert result["no_ask"] == pytest.approx(0.88)

        assert result["volume"] == pytest.approx(10000.0)
        assert result["volume_24h"] == pytest.approx(2500.0)
        assert result["open_interest"] == pytest.approx(5000.0)

        assert result["raw_field_source"]["yes_bid"] == "yes_bid_legacy_cents"
        assert result["raw_field_source"]["volume"] == "volume_legacy"

    def test_legacy_dollars_heuristic(self):
        """Legacy fields that are already in dollars (value <= 1.0) should not be divided."""
        raw = {
            "ticker": "TEST",
            "yes_bid": 0.12,
            "yes_ask": 0.15,
        }
        result = normalize_kalshi_market(raw)
        assert result["yes_bid"] == pytest.approx(0.12)
        assert result["yes_ask"] == pytest.approx(0.15)
        assert result["raw_field_source"]["yes_bid"] == "yes_bid_legacy_dollars"


class TestMixedResponse:
    """Test case 3: Mixed response (some modern, some legacy)"""

    def test_mixed_modern_prices_legacy_volume(self):
        raw = {
            "ticker": "TEST",
            "yes_bid_dollars": "0.2500",
            "yes_ask_dollars": "0.3000",
            "volume": 5000,  # legacy, no volume_fp
            "open_interest_fp": "10000.00",  # modern
        }
        result = normalize_kalshi_market(raw)

        assert result["yes_bid"] == pytest.approx(0.25)
        assert result["yes_ask"] == pytest.approx(0.30)
        assert result["volume"] == pytest.approx(5000.0)
        assert result["open_interest"] == pytest.approx(10000.0)

        assert result["raw_field_source"]["yes_bid"] == "yes_bid_dollars"
        assert result["raw_field_source"]["volume"] == "volume_legacy"
        assert result["raw_field_source"]["open_interest"] == "open_interest_fp"


class TestMissingFields:
    """Test case 4: Missing bid/ask fields → None, not zero"""

    def test_missing_bid_ask(self):
        raw = {
            "ticker": "TEST",
            "title": "Test Market",
        }
        result = normalize_kalshi_market(raw)

        assert result["yes_bid"] is None
        assert result["yes_ask"] is None
        assert result["no_bid"] is None
        assert result["no_ask"] is None
        assert result["odds"]["yes"] is None
        assert result["odds"]["no"] is None
        assert result["volume"] is None
        assert result["volume_24h"] is None
        assert result["open_interest"] is None
        assert result["liquidity_usd"] is None

    def test_missing_bid_but_ask_present(self):
        raw = {
            "ticker": "TEST",
            "yes_ask_dollars": "0.4500",
        }
        result = normalize_kalshi_market(raw)

        assert result["yes_bid"] is None
        assert result["yes_ask"] == pytest.approx(0.45)
        assert result["odds"]["yes"] == pytest.approx(0.45)  # ask only
        assert result["odds"]["no"] == pytest.approx(0.55)


class TestPresentZero:
    """Test case 5: Zero values that are genuinely present → 0.0, not None"""

    def test_present_zero_dollars(self):
        raw = {
            "ticker": "TEST",
            "yes_bid_dollars": "0.0000",
            "yes_ask_dollars": "0.0500",
        }
        result = normalize_kalshi_market(raw)

        assert result["yes_bid"] == pytest.approx(0.0)
        assert result["yes_bid"] is not None
        assert result["raw_field_source"]["yes_bid"] == "yes_bid_dollars"

    def test_present_zero_legacy(self):
        raw = {
            "ticker": "TEST",
            "yes_bid": 0,
            "yes_ask": 5,
        }
        result = normalize_kalshi_market(raw)

        assert result["yes_bid"] == pytest.approx(0.0)
        assert result["yes_bid"] is not None
        # Zero is ambiguous (0 cents == 0 dollars); heuristic routes to dollars path
        assert result["raw_field_source"]["yes_bid"] == "yes_bid_legacy_dollars"


class TestOddsConstraints:
    """Test case 7: odds.yes and odds.no are always floats between 0.0 and 1.0 when present"""

    def test_odds_in_range(self):
        raw = {
            "ticker": "TEST",
            "yes_bid_dollars": "0.1000",
            "yes_ask_dollars": "0.2000",
        }
        result = normalize_kalshi_market(raw)
        assert 0.0 <= result["odds"]["yes"] <= 1.0
        assert 0.0 <= result["odds"]["no"] <= 1.0

    def test_odds_sum_to_one(self):
        raw = {
            "ticker": "TEST",
            "yes_bid_dollars": "0.3000",
            "yes_ask_dollars": "0.4000",
        }
        result = normalize_kalshi_market(raw)
        assert result["odds"]["yes"] + result["odds"]["no"] == pytest.approx(1.0)


class TestRawFieldSource:
    """Test case 8: raw_field_source accurately reflects which path was taken"""

    def test_all_modern(self):
        raw = {
            "ticker": "TEST",
            "yes_bid_dollars": "0.1000",
            "volume_fp": "1000.00",
        }
        result = normalize_kalshi_market(raw)
        src = result["raw_field_source"]
        assert src["yes_bid"] == "yes_bid_dollars"
        assert src["volume"] == "volume_fp"
        assert src["volume_24h"] == "volume_24h_missing"

    def test_all_legacy(self):
        raw = {
            "ticker": "TEST",
            "yes_bid": 10,
            "volume": 1000,
        }
        result = normalize_kalshi_market(raw)
        src = result["raw_field_source"]
        assert src["yes_bid"] == "yes_bid_legacy_cents"
        assert src["volume"] == "volume_legacy"
        assert src["yes_ask"] == "yes_ask_missing"


class TestStringParsing:
    """Test case 9: String-typed *_dollars and *_fp fields are parsed correctly"""

    def test_dollar_string_with_leading_zero(self):
        raw = {"ticker": "TEST", "yes_bid_dollars": "0.0500"}
        result = normalize_kalshi_market(raw)
        assert result["yes_bid"] == pytest.approx(0.05)

    def test_fp_string_large_number(self):
        raw = {"ticker": "TEST", "volume_fp": "1234567.89"}
        result = normalize_kalshi_market(raw)
        assert result["volume"] == pytest.approx(1234567.89)

    def test_dollar_string_no_decimal(self):
        raw = {"ticker": "TEST", "yes_bid_dollars": "1"}
        result = normalize_kalshi_market(raw)
        assert result["yes_bid"] == pytest.approx(1.0)


class TestLiquidityDerivation:
    def test_liquidity_from_reported(self):
        raw = {
            "ticker": "TEST",
            "liquidity_dollars": "10000.00",
            "open_interest_fp": "5000.00",
            "yes_bid_dollars": "0.5000",
            "yes_ask_dollars": "0.5000",
        }
        result = normalize_kalshi_market(raw)
        assert result["liquidity_usd"] == pytest.approx(10000.00)

    def test_liquidity_derived_from_oi(self):
        raw = {
            "ticker": "TEST",
            "open_interest_fp": "10000.00",
            "yes_bid_dollars": "0.2500",
            "yes_ask_dollars": "0.3500",
        }
        result = normalize_kalshi_market(raw)
        # midpoint = 0.30, OI = 10000 -> liquidity = 3000
        assert result["liquidity_usd"] == pytest.approx(3000.00)


class TestCompatibilityKeys:
    """Ensure the output shape matches what existing strategy code expects."""

    def test_has_all_strategy_keys(self):
        raw = {
            "ticker": "TEST",
            "short_name": "Test",
            "yes_bid_dollars": "0.2000",
            "yes_ask_dollars": "0.3000",
            "volume_fp": "1000.00",
        }
        result = normalize_kalshi_market(raw)

        # These are the keys strategies read
        assert "id" in result
        assert "odds" in result
        assert "yes" in result["odds"]
        assert "no" in result["odds"]
        assert "liquidity_usd" in result
        assert "hours_to_end" in result


class TestMarketPriceValue:
    """Tests for utils/kalshi.py _market_price_value() zero-handling fix."""

    def test_present_zero_string_returns_zero(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid_dollars": "0.0000"}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result == pytest.approx(0.0)
        assert result is not None

    def test_present_zero_int_returns_zero(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid": 0}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result == pytest.approx(0.0)
        assert result is not None

    def test_missing_returns_none(self):
        from utils.kalshi import _market_price_value
        market = {}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result is None

    def test_none_value_returns_none(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid_dollars": None}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result is None

    def test_empty_string_returns_none(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid_dollars": ""}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result is None

    def test_positive_string_returns_float(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid_dollars": "0.1200"}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result == pytest.approx(0.12)

    def test_legacy_cents_converted(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid": 12}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result == pytest.approx(0.12)

    def test_legacy_dollars_not_divided(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid": 0.12}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result == pytest.approx(0.12)

    def test_modern_preferred_over_legacy(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid_dollars": "0.2500", "yes_bid": 30}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result == pytest.approx(0.25)

    def test_present_zero_modern_not_falling_back_to_legacy(self):
        from utils.kalshi import _market_price_value
        market = {"yes_bid_dollars": "0.0000", "yes_bid": 5}
        result = _market_price_value(market, "yes_bid_dollars", "yes_bid")
        assert result == pytest.approx(0.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
