"""Tests for the weather runner's live order path and safety limits."""

from unittest.mock import MagicMock, patch

import pytest

from scripts import run_weather_strategy as runner


def _signal(**overrides):
    signal = {
        "ticker": "KXHIGHNY-26JUL02-B99.5",
        "city": "NYC",
        "city_code": "NY",
        "threshold": 99.5,
        "ensemble_prob": 0.80,
        "market_price": 0.42,
        "edge": 0.38,
        "edge_pct": 0.38,
        "confidence": 0.80,
        "volume": 100.0,
        "spread_cents": 4.0,
        "position_usd": 1.00,
        "side": "yes",
        "kelly_fraction": 0.01,
        "hours_to_close": 12.0,
    }
    signal.update(overrides)
    return signal


@pytest.fixture
def no_side_effects(monkeypatch):
    """Stub out DB writes, Discord sends, and daily-risk lookups."""
    record = MagicMock()
    notify = MagicMock()
    monkeypatch.setattr(runner, "record_trade", record)
    monkeypatch.setattr(runner, "notify_weather_order_placed", notify)
    monkeypatch.setattr(runner, "weather_daily_risk_usd", lambda: 0.0)
    return record, notify


def _mock_client():
    client = MagicMock()
    client.place_order.return_value = {"order_id": "test-123", "status": "resting"}
    return client


class TestWeatherLiveOrder:
    def test_weather_live_order(self, no_side_effects):
        """Live mode places a limit order with the signal's price/side/quantity."""
        client = _mock_client()

        placed = runner.execute_weather_orders(client, [_signal()], dry_run=False)

        assert placed == 1
        client.place_order.assert_called_once_with(
            ticker="KXHIGHNY-26JUL02-B99.5",
            side="yes",
            quantity=1,
            price_cents=42,
        )

    def test_weather_live_quantity_clamped_to_client_max(self, no_side_effects):
        """Computed quantity never exceeds the order client's hard cap."""
        client = _mock_client()
        # $1.00 at 30c would naively be 3 contracts; client caps at MAX_QUANTITY
        signal = _signal(market_price=0.30, position_usd=1.00)

        runner.execute_weather_orders(client, [signal], dry_run=False)

        quantity = client.place_order.call_args.kwargs["quantity"]
        assert quantity <= runner.KalshiOrderClient.MAX_QUANTITY

    def test_weather_no_side_uses_yes_price(self, no_side_effects):
        """NO-side orders pass the YES price; cost gate uses the NO cost."""
        client = _mock_client()
        # NO at yes-price 53.5c → cost 46.5c, above the 25c minimum
        signal = _signal(side="no", market_price=0.535, ensemble_prob=0.0)

        placed = runner.execute_weather_orders(client, [signal], dry_run=False)

        assert placed == 1
        assert client.place_order.call_args.kwargs["price_cents"] == 54
        assert client.place_order.call_args.kwargs["side"] == "no"


class TestWeatherDryRun:
    def test_weather_dry_run_no_order(self, no_side_effects):
        """Dry-run never calls place_order, record_trade, or Discord."""
        record, notify = no_side_effects
        client = _mock_client()

        placed = runner.execute_weather_orders(client, [_signal()], dry_run=True)

        assert placed == 1  # would-place counted
        client.place_order.assert_not_called()
        record.assert_not_called()
        notify.assert_not_called()

    def test_weather_env_dry_run_forces_dry_run(self, monkeypatch, no_side_effects):
        """WEATHER_DRY_RUN=true forces dry-run even without --dry-run flag."""
        monkeypatch.setenv("WEATHER_DRY_RUN", "true")
        monkeypatch.setattr(runner, "setup_logging", lambda: "/tmp/test.log")
        monkeypatch.setattr(runner, "generate_proof", MagicMock())
        monkeypatch.setattr(runner, "generate_weather_signals", lambda **kw: [_signal()])
        get_client = MagicMock()
        monkeypatch.setattr(runner, "get_order_client", get_client)
        monkeypatch.setattr("sys.argv", ["run_weather_strategy.py"])

        assert runner.main() == 0
        get_client.assert_not_called()


class TestWeatherSafetyLimits:
    def test_weather_max_orders_per_run(self, monkeypatch, no_side_effects):
        """No more than WEATHER_MAX_ORDERS_PER_RUN orders per invocation."""
        monkeypatch.setattr(runner, "WEATHER_MAX_ORDERS_PER_RUN", 2)
        monkeypatch.setattr(runner, "WEATHER_MAX_NOTIONAL_PER_RUN_USD", 10.0)
        monkeypatch.setattr(runner, "WEATHER_MAX_DAILY_LOSS_USD", 10.0)
        client = _mock_client()
        signals = [_signal(ticker=f"KXHIGHNY-26JUL02-B{i}") for i in range(5)]

        placed = runner.execute_weather_orders(client, signals, dry_run=False)

        assert placed == 2
        assert client.place_order.call_count == 2

    def test_weather_min_trade_price_rejected(self, no_side_effects):
        """Orders costing less than WEATHER_MIN_TRADE_PRICE_CENTS are skipped."""
        client = _mock_client()
        signal = _signal(market_price=0.08)  # 8c yes < 25c minimum

        placed = runner.execute_weather_orders(client, [signal], dry_run=False)

        assert placed == 0
        client.place_order.assert_not_called()

    def test_weather_per_run_notional_cap(self, monkeypatch, no_side_effects):
        """Cumulative run notional stays within WEATHER_MAX_NOTIONAL_PER_RUN_USD."""
        monkeypatch.setattr(runner, "WEATHER_MAX_ORDERS_PER_RUN", 5)
        monkeypatch.setattr(runner, "WEATHER_MAX_NOTIONAL_PER_RUN_USD", 1.00)
        monkeypatch.setattr(runner, "WEATHER_MAX_DAILY_LOSS_USD", 10.0)
        client = _mock_client()
        # Each order costs 60c; the second would push the run to $1.20 > $1.00
        signals = [
            _signal(ticker="KXHIGHNY-26JUL02-B1", market_price=0.60),
            _signal(ticker="KXHIGHNY-26JUL02-B2", market_price=0.60),
        ]

        placed = runner.execute_weather_orders(client, signals, dry_run=False)

        assert placed == 1
        assert client.place_order.call_count == 1

    def test_weather_daily_loss_limit_blocks_run(self, monkeypatch, no_side_effects):
        """No orders when today's weather risk already meets the daily cap."""
        monkeypatch.setattr(runner, "weather_daily_risk_usd", lambda: 1.00)
        monkeypatch.setattr(runner, "WEATHER_MAX_DAILY_LOSS_USD", 1.00)
        client = _mock_client()

        placed = runner.execute_weather_orders(client, [_signal()], dry_run=False)

        assert placed == 0
        client.place_order.assert_not_called()

    def test_weather_limits_independent_of_main_bot(self, no_side_effects):
        """Weather limits come from WEATHER_* env, not the main bot's vars."""
        assert runner.WEATHER_MAX_ORDERS_PER_RUN == 2
        assert runner.WEATHER_MAX_DAILY_LOSS_USD == 1.00
        assert runner.WEATHER_MAX_NOTIONAL_PER_RUN_USD == 1.00
        assert runner.WEATHER_MIN_TRADE_PRICE_CENTS == 25


class TestWeatherPnlRecording:
    def test_weather_pnl_recording(self, no_side_effects):
        """Live trades are recorded with the weather category markers."""
        record, notify = no_side_effects
        client = _mock_client()

        runner.execute_weather_orders(client, [_signal()], dry_run=False)

        record.assert_called_once()
        kwargs = record.call_args.kwargs
        assert kwargs["phase"] == "weather"
        assert kwargs["market_category"] == "weather"
        assert kwargs["signal_type"] == "gfs_ensemble"
        assert kwargs["action"] == "BUY"
        assert kwargs["size_usd"] == pytest.approx(0.42)

    def test_weather_discord_includes_city_and_ensemble(self, no_side_effects):
        """Weather Discord alert carries city, ensemble prob, and threshold."""
        record, notify = no_side_effects
        client = _mock_client()

        runner.execute_weather_orders(client, [_signal()], dry_run=False)

        notify.assert_called_once()
        kwargs = notify.call_args.kwargs
        assert kwargs["city"] == "NYC"
        assert kwargs["ensemble_prob"] == pytest.approx(0.80)
        assert kwargs["threshold"] == pytest.approx(99.5)
        assert kwargs["market_price"] == pytest.approx(0.42)
