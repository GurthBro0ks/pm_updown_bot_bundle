"""Tests for the weather runner's live order path and safety limits."""

from unittest.mock import MagicMock, patch

import pytest

from scripts import run_weather_strategy as runner


def _set_safe_live_env(monkeypatch):
    monkeypatch.setenv("WEATHER_MAX_ORDERS_PER_RUN", "1")
    monkeypatch.setenv("WEATHER_MAX_ORDER_USD", "0.25")
    monkeypatch.setenv("WEATHER_MAX_RUN_EXPOSURE_USD", "1.00")
    monkeypatch.setenv("WEATHER_MAX_OPEN_EXPOSURE_USD", "2.00")


def _signal(**overrides):
    signal = {
        "ticker": "KXHIGHNY-26JUL02-B99.5",
        "city": "NYC",
        "city_code": "NY",
        "threshold": 99.5,
        "ensemble_prob": 0.80,
        "market_price": 0.25,
        "edge": 0.55,
        "edge_pct": 0.55,
        "confidence": 0.80,
        "volume": 100.0,
        "spread_cents": 4.0,
        "position_usd": 0.25,
        "side": "yes",
        "kelly_fraction": 0.01,
        "hours_to_close": 12.0,
    }
    signal.update(overrides)
    return signal


@pytest.fixture
def no_side_effects(monkeypatch):
    """Stub out DB writes, Discord sends, and daily-risk lookups."""
    _set_safe_live_env(monkeypatch)
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
            price_cents=25,
        )

    def test_weather_live_quantity_clamped_to_client_max(self, no_side_effects):
        """Computed quantity never exceeds the order client's hard cap."""
        client = _mock_client()
        # $1.00 at 25c would naively be 4 contracts; client caps at MAX_QUANTITY.
        signal = _signal(market_price=0.25, position_usd=1.00)

        runner.execute_weather_orders(client, [signal], dry_run=False)

        quantity = client.place_order.call_args.kwargs["quantity"]
        assert quantity <= runner.KalshiOrderClient.MAX_QUANTITY

    def test_weather_no_side_uses_yes_price(self, no_side_effects):
        """NO-side orders pass the YES price; cost gate uses the NO cost."""
        client = _mock_client()
        # NO at yes-price 75c -> cost 25c, within the tiny max-order cap.
        signal = _signal(side="no", market_price=0.75, ensemble_prob=0.0)

        placed = runner.execute_weather_orders(client, [signal], dry_run=False)

        assert placed == 1
        assert client.place_order.call_args.kwargs["price_cents"] == 75
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

    def test_weather_env_unset_defaults_to_dry_run(self, monkeypatch, no_side_effects):
        """Unset WEATHER_DRY_RUN fails closed to dry-run."""
        monkeypatch.delenv("WEATHER_DRY_RUN", raising=False)
        monkeypatch.setattr(runner, "setup_logging", lambda: "/tmp/test.log")
        monkeypatch.setattr(runner, "generate_proof", MagicMock())
        monkeypatch.setattr(runner, "generate_weather_signals", lambda **kw: [_signal()])
        get_client = MagicMock()
        monkeypatch.setattr(runner, "get_order_client", get_client)
        monkeypatch.setattr("sys.argv", ["run_weather_strategy.py"])

        assert runner.main() == 0
        get_client.assert_not_called()

    def test_weather_env_false_without_live_enabled_stays_dry_run(self, monkeypatch, no_side_effects):
        """WEATHER_DRY_RUN=false alone is not enough to go live."""
        monkeypatch.setenv("WEATHER_DRY_RUN", "false")
        monkeypatch.delenv("WEATHER_LIVE_ENABLED", raising=False)
        monkeypatch.setattr(runner, "setup_logging", lambda: "/tmp/test.log")
        monkeypatch.setattr(runner, "generate_proof", MagicMock())
        monkeypatch.setattr(runner, "generate_weather_signals", lambda **kw: [_signal()])
        get_client = MagicMock()
        monkeypatch.setattr(runner, "get_order_client", get_client)
        monkeypatch.setattr("sys.argv", ["run_weather_strategy.py"])

        assert runner.main() == 0
        get_client.assert_not_called()

    def test_weather_live_requires_false_and_live_enabled(self, monkeypatch, no_side_effects):
        """Live mode requires WEATHER_DRY_RUN=false plus WEATHER_LIVE_ENABLED=true."""
        client = _mock_client()
        execute = MagicMock(return_value=1)
        monkeypatch.setenv("WEATHER_DRY_RUN", "false")
        monkeypatch.setenv("WEATHER_LIVE_ENABLED", "true")
        monkeypatch.setattr(runner, "setup_logging", lambda: "/tmp/test.log")
        monkeypatch.setattr(runner, "generate_proof", MagicMock())
        monkeypatch.setattr(runner, "generate_weather_signals", lambda **kw: [_signal()])
        monkeypatch.setattr(runner, "get_order_client", MagicMock(return_value=client))
        monkeypatch.setattr(runner, "count_open_weather_orders", lambda _client: 0)
        monkeypatch.setattr(runner, "count_weather_trades_today", lambda: 0)
        monkeypatch.setattr(runner, "execute_weather_orders", execute)
        monkeypatch.setattr("sys.argv", ["run_weather_strategy.py"])

        assert runner.main() == 0
        assert execute.call_args.kwargs["dry_run"] is False


class TestWeatherSafetyLimits:
    def test_weather_max_orders_per_run(self, monkeypatch, no_side_effects):
        """No more than WEATHER_MAX_ORDERS_PER_RUN orders per invocation."""
        monkeypatch.setenv("WEATHER_MAX_ORDERS_PER_RUN", "1")
        monkeypatch.setenv("WEATHER_MAX_ORDER_USD", "0.25")
        monkeypatch.setenv("WEATHER_MAX_RUN_EXPOSURE_USD", "1.00")
        monkeypatch.setenv("WEATHER_MAX_OPEN_EXPOSURE_USD", "2.00")
        monkeypatch.setattr(runner, "WEATHER_MAX_DAILY_LOSS_USD", 10.0)
        client = _mock_client()
        signals = [_signal(ticker=f"KXHIGHNY-26JUL02-B{i}") for i in range(5)]

        placed = runner.execute_weather_orders(client, signals, dry_run=False)

        assert placed == 1
        assert client.place_order.call_count == 1

    def test_weather_min_trade_price_rejected(self, no_side_effects):
        """Orders costing less than WEATHER_MIN_TRADE_PRICE_CENTS are skipped."""
        client = _mock_client()
        signal = _signal(market_price=0.08)  # 8c yes < 25c minimum

        placed = runner.execute_weather_orders(client, [signal], dry_run=False)

        assert placed == 0
        client.place_order.assert_not_called()

    def test_weather_per_run_notional_cap(self, monkeypatch, no_side_effects):
        """Cumulative run notional stays within WEATHER_MAX_NOTIONAL_PER_RUN_USD."""
        monkeypatch.setenv("WEATHER_MAX_ORDERS_PER_RUN", "1")
        monkeypatch.setenv("WEATHER_MAX_ORDER_USD", "0.25")
        monkeypatch.setenv("WEATHER_MAX_RUN_EXPOSURE_USD", "1.00")
        monkeypatch.setenv("WEATHER_MAX_OPEN_EXPOSURE_USD", "2.00")
        monkeypatch.setattr(runner, "WEATHER_MAX_DAILY_LOSS_USD", 10.0)
        client = _mock_client()
        # The order cap stops the second tiny order even though run exposure remains safe.
        signals = [
            _signal(ticker="KXHIGHNY-26JUL02-B1", market_price=0.25),
            _signal(ticker="KXHIGHNY-26JUL02-B2", market_price=0.25),
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
        assert runner.WEATHER_MAX_ORDERS_PER_RUN == 1
        assert runner.WEATHER_MAX_ORDER_USD == 0.25
        assert runner.WEATHER_MAX_RUN_EXPOSURE_USD == 1.00
        assert runner.WEATHER_MAX_OPEN_EXPOSURE_USD == 2.00
        assert runner.WEATHER_MAX_DAILY_LOSS_USD == 1.00
        assert runner.WEATHER_MIN_TRADE_PRICE_CENTS == 25

    def test_weather_live_missing_limit_fails_closed(self, monkeypatch, no_side_effects):
        """Live execution refuses to trade if a required tiny limit is absent."""
        monkeypatch.delenv("WEATHER_MAX_ORDER_USD", raising=False)
        client = _mock_client()

        with pytest.raises(runner.WeatherLiveLimitError):
            runner.execute_weather_orders(client, [_signal()], dry_run=False)

        client.place_order.assert_not_called()

    def test_weather_live_malformed_limit_fails_closed(self, monkeypatch, no_side_effects):
        """Live execution refuses malformed tiny-limit values."""
        monkeypatch.setenv("WEATHER_MAX_ORDER_USD", "not-a-number")
        client = _mock_client()

        with pytest.raises(runner.WeatherLiveLimitError):
            runner.execute_weather_orders(client, [_signal()], dry_run=False)

        client.place_order.assert_not_called()

    def test_weather_live_oversized_limit_fails_closed(self, monkeypatch, no_side_effects):
        """Live execution refuses limits above the hard-coded weather caps."""
        monkeypatch.setenv("WEATHER_MAX_ORDERS_PER_RUN", "2")
        client = _mock_client()

        with pytest.raises(runner.WeatherLiveLimitError):
            runner.execute_weather_orders(client, [_signal()], dry_run=False)

        client.place_order.assert_not_called()

    def test_weather_order_cost_cap(self, monkeypatch, no_side_effects):
        """A live weather order cannot cost more than $0.25."""
        client = _mock_client()
        signal = _signal(market_price=0.26, position_usd=0.26)

        placed = runner.execute_weather_orders(client, [signal], dry_run=False)

        assert placed == 0
        client.place_order.assert_not_called()

    def test_weather_open_exposure_cap_blocks_run(self, monkeypatch, no_side_effects):
        """Open weather exposure is capped separately from main-bot limits."""
        client = _mock_client()
        client.get_orders.return_value = [
            {
                "market_ticker": "KXHIGHNY-26JUL02-B99.5",
                "side": "yes",
                "remaining_count": 8,
                "price_cents": 25,
            }
        ]
        monkeypatch.setenv("WEATHER_DRY_RUN", "false")
        monkeypatch.setenv("WEATHER_LIVE_ENABLED", "true")
        monkeypatch.setattr(runner, "setup_logging", lambda: "/tmp/test.log")
        monkeypatch.setattr(runner, "generate_proof", MagicMock())
        monkeypatch.setattr(runner, "generate_weather_signals", lambda **kw: [_signal()])
        monkeypatch.setattr(runner, "get_order_client", MagicMock(return_value=client))
        monkeypatch.setattr("sys.argv", ["run_weather_strategy.py"])

        assert runner.main() == 0
        client.place_order.assert_not_called()


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
        assert kwargs["size_usd"] == pytest.approx(0.25)

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
        assert kwargs["market_price"] == pytest.approx(0.25)
