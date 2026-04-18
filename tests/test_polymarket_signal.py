import json
import time
from unittest.mock import MagicMock, patch

import pytest

from providers.polymarket_signal import (
    _cache,
    _extract_search_terms,
    _similarity,
    find_matching_market,
    format_poly_context,
    get_market_price,
    search_markets,
    enrich_market_text,
)


class TestSimilarity:
    def test_identical(self):
        assert _similarity("hello", "hello") == 1.0

    def test_similar(self):
        score = _similarity("S&P 500 above 5000", "Will S&P 500 close above 5000?")
        assert score > 0.5

    def test_different(self):
        score = _similarity("Bitcoin price", "Who will win the election?")
        assert score < 0.3


class TestExtractSearchTerms:
    def test_sp500_ticker(self):
        result = _extract_search_terms("KXINX-26APR17-B7012")
        assert result is not None
        assert "S&P" in result

    def test_btc_ticker(self):
        result = _extract_search_terms("KXBTC-26APR17")
        assert result is not None
        assert "Bitcoin" in result

    def test_eth_ticker(self):
        result = _extract_search_terms("KXETHY-27JAN0100-B3125")
        assert result is not None
        assert "Ethereum" in result

    def test_nasdaq_ticker(self):
        assert _extract_search_terms("KXNASDAQ100U-26APR20") == "NASDAQ"

    def test_sports_ticker_skipped(self):
        assert _extract_search_terms("KXMVESPORTS-ABC") is None

    def test_unknown_with_title(self):
        terms = _extract_search_terms("UNKNOWN", "Will inflation exceed 3% in 2026?")
        assert terms is not None

    def test_unknown_no_title(self):
        assert _extract_search_terms("ZZZZ") is None


class TestGetMarketPrice:
    def test_normal_market(self):
        market = {
            "question": "Will S&P close above 5000?",
            "outcomePrices": json.dumps([0.65, 0.35]),
            "volume": 1000000,
            "liquidity": 500000,
            "slug": "sp500-above-5000",
            "endDate": "2026-04-30",
        }
        result = get_market_price(market)
        assert result["yes_price"] == 0.65
        assert result["no_price"] == 0.35
        assert result["volume"] == 1000000
        assert "polymarket.com" in result["url"]

    def test_empty_market(self):
        result = get_market_price({})
        assert result.get("yes_price") is None

    def test_malformed_prices(self):
        result = get_market_price({"outcomePrices": "invalid"})
        assert isinstance(result, dict)

    def test_list_prices(self):
        market = {"outcomePrices": [0.8, 0.2]}
        result = get_market_price(market)
        assert result["yes_price"] == 0.8


class TestFormatPolyContext:
    def test_full_match(self):
        match = {
            "question": "S&P above 5000?",
            "yes_price": 0.72,
            "delta": 0.05,
            "delta_pct": 7.5,
            "volume": 500000,
            "match_score": 0.85,
        }
        text = format_poly_context(match)
        assert "Cross-Venue Signal" in text
        assert "0.720" in text
        assert "higher" in text

    def test_none_match(self):
        assert format_poly_context(None) == ""

    def test_no_delta(self):
        match = {"question": "Test?", "yes_price": 0.5, "delta": None, "match_score": 0.6}
        text = format_poly_context(match)
        assert "Cross-Venue Signal" in text
        assert "higher" not in text

    def test_negative_delta(self):
        match = {
            "question": "BTC above 80k?",
            "yes_price": 0.45,
            "delta": -0.10,
            "delta_pct": -18.2,
            "volume": 100000,
            "match_score": 0.7,
        }
        text = format_poly_context(match)
        assert "lower" in text


class TestCache:
    def test_cache_prevents_duplicate_requests(self):
        _cache.clear()
        with patch("providers.polymarket_signal.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.status_code = 200
            mock_resp.json.return_value = []
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            search_markets("test query")
            search_markets("test query")
            assert mock_get.call_count == 1

        _cache.clear()


class TestFindMatchingMarket:
    def test_no_search_terms(self):
        _cache.clear()
        result = find_matching_market("KXGOV")
        assert result is None
        _cache.clear()

    @patch("providers.polymarket_signal.search_markets")
    def test_no_matches(self, mock_search):
        _cache.clear()
        mock_search.return_value = []
        result = find_matching_market("KXINX", "S&P 500 above 7000")
        assert result is None
        _cache.clear()

    @patch("providers.polymarket_signal.search_markets")
    def test_match_with_delta(self, mock_search):
        _cache.clear()
        mock_search.return_value = [
            {
                "question": "Will S&P 500 close above 7000 on April 18?",
                "outcomePrices": json.dumps([0.72, 0.28]),
                "volume": 100000,
                "slug": "sp500-7000",
            }
        ]
        result = find_matching_market(
            "KXINX-26APR18-B7000", "S&P 500 above 7000", kalshi_yes_price=0.68,
        )
        assert result is not None
        assert result["yes_price"] == 0.72
        assert result["delta"] == pytest.approx(0.04, abs=0.01)
        assert result["delta_pct"] > 0
        _cache.clear()

    @patch("providers.polymarket_signal.search_markets")
    def test_low_similarity_rejected(self, mock_search):
        _cache.clear()
        mock_search.return_value = [
            {
                "question": "Will it rain in Tokyo tomorrow?",
                "outcomePrices": json.dumps([0.5, 0.5]),
                "slug": "tokyo-rain",
            }
        ]
        result = find_matching_market("KXBTC-26APR18", "Bitcoin above 80k")
        assert result is None
        _cache.clear()

    @patch("providers.polymarket_signal.search_markets")
    def test_strike_price_boosts_score(self, mock_search):
        _cache.clear()
        mock_search.return_value = [
            {
                "question": "Will the price of Bitcoin be above $80,000 on April 18?",
                "outcomePrices": json.dumps([0.35, 0.65]),
                "volume": 500000,
                "slug": "btc-80k",
            }
        ]
        result = find_matching_market("KXBTC-26APR18-T80000", "Bitcoin above 80000", kalshi_yes_price=0.30)
        assert result is not None
        assert result["yes_price"] == 0.35
        assert result["delta"] == pytest.approx(0.05, abs=0.01)
        _cache.clear()


class TestEnrichMarketText:
    @patch("providers.polymarket_signal.find_matching_market")
    def test_enrichment_appended(self, mock_find):
        mock_find.return_value = {
            "question": "BTC above 80k?",
            "yes_price": 0.65,
            "delta": 0.05,
            "delta_pct": 8.3,
            "volume": 500000,
            "match_score": 0.8,
        }
        result = enrich_market_text("What is the Bitcoin price?", "KXBTC-26APR18")
        assert "Cross-Venue Signal" in result
        assert "What is the Bitcoin price?" in result

    @patch("providers.polymarket_signal.find_matching_market")
    def test_no_match_returns_original(self, mock_find):
        mock_find.return_value = None
        original = "What is the Bitcoin price?"
        result = enrich_market_text(original, "KXBTC-26APR18")
        assert result == original
