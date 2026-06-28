from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.weather_markets import discover_weather_markets
from strategies.weather_signals import generate_weather_signals


def _future_close_time(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def test_weather_markets_modern_fields(monkeypatch):
    """Weather discovery reads Kalshi modern dollar/fixed-point fields."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "markets": [
            {
                "ticker": "KXHIGHNY-26JUN29-B80",
                "title": "NYC high above 80",
                "yes_bid_dollars": "0.2400",
                "yes_ask_dollars": "0.2800",
                "volume_fp": "123.45",
                "open_interest_fp": "67.89",
                "close_time": _future_close_time(),
            }
        ]
    }

    monkeypatch.setattr("data.weather_markets.WEATHER_SERIES_TICKERS", ["KXHIGHNY"])
    monkeypatch.setattr("data.weather_markets._get_auth", lambda: ("test-key", object()))
    monkeypatch.setattr("data.weather_markets.get_kalshi_headers", lambda *args, **kwargs: {})
    monkeypatch.setattr("data.weather_markets.requests.get", lambda *args, **kwargs: response)

    markets = discover_weather_markets()

    assert len(markets) == 1
    market = markets[0]
    assert market["yes_bid"] == pytest.approx(0.24)
    assert market["yes_ask"] == pytest.approx(0.28)
    assert isinstance(market["yes_bid"], float)
    assert isinstance(market["yes_ask"], float)
    assert market["mid_price"] == pytest.approx(0.26)
    assert market["volume"] == pytest.approx(123.45)
    assert market["open_interest"] == pytest.approx(67.89)


def test_weather_signals_with_modern_markets(monkeypatch):
    """Signal generation consumes normalized weather markets without raw API fields."""
    markets = [
        {
            "ticker": "KXHIGHNY-26JUN29-B80",
            "city_code": "NY",
            "city_name": "NYC",
            "threshold": 80.0,
            "mid_price": 0.50,
            "volume": 100.0,
            "spread_cents": 4.0,
            "hours_to_close": 24.0,
            "date_str": "26JUN29",
            "comparison": "above",
            "yes_bid": 0.48,
            "yes_ask": 0.52,
        }
    ]
    daily_highs = [81.0] * 25 + [72.0] * 6

    monkeypatch.setattr("strategies.weather_signals.discover_weather_markets", lambda **kwargs: markets)
    monkeypatch.setattr("strategies.weather_signals.get_ensemble_forecast_for_city_code", lambda city_code: daily_highs)

    signals = generate_weather_signals(bankroll=100.0, dry_run=True)

    assert len(signals) == 1
    signal = signals[0]
    assert signal["ticker"] == "KXHIGHNY-26JUN29-B80"
    assert signal["ensemble_prob"] == pytest.approx(25 / 31)
    assert signal["edge"] == pytest.approx((25 / 31) - 0.50)
    assert signal["side"] == "yes"
    assert signal["yes_bid"] == pytest.approx(0.48)
    assert signal["yes_ask"] == pytest.approx(0.52)


def test_dry_run_flag_does_not_call_kalshi_place_order(monkeypatch):
    """--dry-run must not initialize the order client or place real orders."""
    import scripts.run_weather_strategy as runner

    signal = {
        "ticker": "KXHIGHNY-26JUN29-B80",
        "side": "yes",
        "position_usd": 0.50,
        "ensemble_prob": 0.80,
        "edge_pct": 0.30,
        "market_price": 0.50,
        "kelly_fraction": 0.005,
        "city_code": "NY",
    }

    monkeypatch.setattr(runner, "setup_logging", lambda: "weather-test.log")
    monkeypatch.setattr(runner, "generate_weather_signals", lambda **kwargs: [signal])
    monkeypatch.setattr(runner, "generate_proof", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "get_order_client", MagicMock(side_effect=AssertionError("client initialized in dry-run")))
    monkeypatch.setattr(
        runner.KalshiOrderClient,
        "place_order",
        MagicMock(side_effect=AssertionError("place_order called in dry-run")),
    )
    monkeypatch.setattr("sys.argv", ["run_weather_strategy.py", "--dry-run"])

    assert runner.main() == 0
    runner.get_order_client.assert_not_called()
    runner.KalshiOrderClient.place_order.assert_not_called()


def test_weather_runner_caps_per_city_exposure(monkeypatch):
    import scripts.run_weather_strategy as runner

    signals = [
        {"ticker": "A", "city_code": "NY", "edge_pct": 0.10, "position_usd": 0.75},
        {"ticker": "B", "city_code": "NY", "edge_pct": 0.10, "position_usd": 0.75},
        {"ticker": "C", "city_code": "CHI", "edge_pct": 0.10, "position_usd": 0.75},
    ]

    monkeypatch.setattr(runner, "WEATHER_MAX_EXPOSURE_PER_CITY", 1.00)
    monkeypatch.setattr(runner, "WEATHER_MIN_EDGE_PCT", 3.0)

    selected = runner.apply_weather_risk_limits(signals, max_trades=10)

    assert [s["ticker"] for s in selected] == ["A", "C"]
