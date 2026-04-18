"""Tests for providers/price_feed.py and sentiment_scorer prompt enrichment."""

import importlib
import time
from unittest.mock import MagicMock, patch

import pytest

from providers.price_feed import (
    PriceFeedProvider,
    _compute_volatility_5d,
    _extract_prefix,
    _resolve_yfinance_symbol,
    enrich_market_text,
)


class TestTickerMapping:
    def test_kinx_maps_to_gspc(self):
        assert _resolve_yfinance_symbol("KXINX-26APR18-B7012") == "^GSPC"

    def test_kinxu_maps_to_gspc(self):
        assert _resolve_yfinance_symbol("KXINXU-26APR18-T7099") == "^GSPC"

    def test_inx_maps_to_gspc(self):
        assert _resolve_yfinance_symbol("INX") == "^GSPC"

    def test_kxndx_maps_to_ixic(self):
        assert _resolve_yfinance_symbol("KXNDX-26APR18-B20100") == "^IXIC"

    def test_ndx_maps_to_ixic(self):
        assert _resolve_yfinance_symbol("NDX") == "^IXIC"

    def test_kxbtc_maps_to_btc(self):
        assert _resolve_yfinance_symbol("KXBTC-26APR18-B100000") == "BTC-USD"

    def test_btc_maps_to_btc(self):
        assert _resolve_yfinance_symbol("BTC") == "BTC-USD"

    def test_kxeth_maps_to_eth(self):
        assert _resolve_yfinance_symbol("KXETHY-26APR18-B3000") == "ETH-USD"

    def test_eth_maps_to_eth(self):
        assert _resolve_yfinance_symbol("ETH") == "ETH-USD"


class TestUnknownTicker:
    def test_unknown_returns_none(self):
        assert _resolve_yfinance_symbol("KXGOV-FOO") is None

    def test_empty_returns_none(self):
        assert _resolve_yfinance_symbol("") is None

    def test_none_returns_none(self):
        assert _resolve_yfinance_symbol(None) is None

    def test_unmapped_prefix_returns_none(self):
        assert _resolve_yfinance_symbol("KXSPORTS-FOO") is None


class TestExtractPrefix:
    def test_standard_ticker(self):
        assert _extract_prefix("KXINX-26APR18-B7012") == "KXINX"

    def test_no_suffix(self):
        assert _extract_prefix("KXBTC") == "KXBTC"

    def test_empty(self):
        assert _extract_prefix("") is None


class TestCacheHit:
    @patch("providers.price_feed._fetch_yfinance")
    def test_second_call_returns_cached_data(self, mock_fetch):
        mock_fetch.return_value = {
            "current_price": 5500.0,
            "prev_close": 5480.0,
            "change_pct": 0.36,
            "high_52w": 5600.0,
            "low_52w": 4800.0,
            "last_5d_prices": [5480.0, 5490.0, 5510.0, 5495.0, 5500.0],
            "volatility_5d": 0.25,
            "fetched_at": "2026-04-18T12:00:00+00:00",
            "symbol": "^GSPC",
        }

        import providers.price_feed as pf
        pf._cache.clear()

        provider = PriceFeedProvider(cache_ttl=300)
        ctx1 = provider.get_price_context("KXINX-26APR18-B7012")
        ctx2 = provider.get_price_context("KXINX-26APR18-B9999")

        assert mock_fetch.call_count == 1
        assert ctx1["fetched_at"] == ctx2["fetched_at"]
        assert ctx1["current_price"] == ctx2["current_price"]


class TestPriceContextFormat:
    def test_returned_dict_has_all_expected_keys(self):
        ctx = {
            "current_price": 5500.0,
            "prev_close": 5480.0,
            "change_pct": 0.36,
            "high_52w": 5600.0,
            "low_52w": 4800.0,
            "last_5d_prices": [5480.0, 5490.0, 5510.0, 5495.0, 5500.0],
            "volatility_5d": 0.25,
            "fetched_at": "2026-04-18T12:00:00+00:00",
            "symbol": "^GSPC",
        }

        expected_keys = {
            "current_price", "prev_close", "change_pct",
            "high_52w", "low_52w", "last_5d_prices",
            "volatility_5d", "fetched_at", "symbol",
        }
        assert set(ctx.keys()) == expected_keys

    def test_format_includes_price_and_change(self):
        ctx = {
            "current_price": 5500.0,
            "prev_close": 5480.0,
            "change_pct": 0.36,
            "high_52w": None,
            "low_52w": None,
            "last_5d_prices": [],
            "volatility_5d": 0.0,
            "fetched_at": "2026-04-18T12:00:00+00:00",
            "symbol": "^GSPC",
        }
        text = PriceFeedProvider.format_price_context(ctx)
        assert "$5,500.00" in text
        assert "+0.36%" in text
        assert "$5,480.00" in text

    def test_format_includes_5d_range_and_volatility(self):
        ctx = {
            "current_price": 5500.0,
            "prev_close": 5480.0,
            "change_pct": 0.36,
            "high_52w": None,
            "low_52w": None,
            "last_5d_prices": [5480.0, 5490.0, 5510.0, 5495.0, 5500.0],
            "volatility_5d": 0.25,
            "fetched_at": "2026-04-18T12:00:00+00:00",
            "symbol": "^GSPC",
        }
        text = PriceFeedProvider.format_price_context(ctx)
        assert "5-day range" in text
        assert "$5,480.00" in text
        assert "$5,510.00" in text
        assert "0.25%" in text


class TestPromptEnrichment:
    def test_enriched_prompt_contains_price_data(self):
        fake_ctx = {
            "current_price": 5500.0,
            "prev_close": 5480.0,
            "change_pct": 0.36,
            "high_52w": None,
            "low_52w": None,
            "last_5d_prices": [5480.0, 5490.0, 5510.0, 5495.0, 5500.0],
            "volatility_5d": 0.25,
            "fetched_at": "2026-04-18T12:00:00+00:00",
            "symbol": "^GSPC",
        }

        with patch("providers.price_feed.get_feed_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.get_price_context.return_value = fake_ctx
            mock_get.return_value = mock_provider

            result = enrich_market_text(
                "Will the S&P 500 close above 7000?",
                market_ticker="KXINX-26APR18-B7012",
            )

        assert "Will the S&P 500 close above 7000?" in result
        assert "Current Market Data" in result
        assert "$5,500.00" in result

    def test_no_enrichment_for_unknown_ticker(self):
        result = enrich_market_text(
            "Will it rain tomorrow?",
            market_ticker="KXWEATHER-FOO",
        )
        assert result == "Will it rain tomorrow?"

    def test_no_enrichment_without_ticker(self):
        result = enrich_market_text("Some market text", market_ticker=None)
        assert result == "Some market text"


class TestVolatilityComputation:
    def test_zero_volatility_constant_prices(self):
        assert _compute_volatility_5d([100.0, 100.0, 100.0, 100.0]) == 0.0

    def test_nonzero_volatility(self):
        vol = _compute_volatility_5d([100.0, 102.0, 98.0, 101.0, 99.0])
        assert vol > 0.0

    def test_single_price_returns_zero(self):
        assert _compute_volatility_5d([100.0]) == 0.0

    def test_empty_returns_zero(self):
        assert _compute_volatility_5d([]) == 0.0
