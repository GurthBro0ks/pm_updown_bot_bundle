from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import sys

sys.path.insert(0, "/opt/slimy/pm_updown_bot_bundle")

from strategies import kalshi_optimize


def _market():
    close_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return {
        "id": "KXINXU-26JUL03H1600-T7500",
        "ticker": "KXINXU-26JUL03H1600-T7500",
        "title": "Will the S&P close above 7500?",
        "odds": {"yes": 0.50},
        "volume_24h": 1000,
        "liquidity_usd": 1000,
        "close_time": close_time,
        "series_category": "index",
        "price_history": [],
    }


def _reset_strategy_state():
    for attr in (
        "_first_order_placed",
        "_dedup_fetched",
        "_existing_tickers",
        "_existing_orders_count",
        "_orders_placed_this_run",
        "_notional_this_run",
    ):
        if hasattr(kalshi_optimize.optimize_kalshi_strategy, attr):
            delattr(kalshi_optimize.optimize_kalshi_strategy, attr)


def _patch_common(monkeypatch):
    _reset_strategy_state()
    monkeypatch.setattr(kalshi_optimize, "fetch_kalshi_markets", lambda: [_market()])
    monkeypatch.setattr(kalshi_optimize, "filter_low_liquidity_markets", lambda markets, **_: markets)
    monkeypatch.setattr(kalshi_optimize, "estimate_true_price", lambda *_, **__: 0.80)
    monkeypatch.setattr(kalshi_optimize, "_apply_vol_model_or_shrinkage", lambda market, prob: prob)
    monkeypatch.setattr(kalshi_optimize, "_was_last_prior_fallback", None)
    monkeypatch.setattr(kalshi_optimize, "validate_prior", None)
    monkeypatch.setattr(kalshi_optimize, "find_best_maker_market", lambda *_: None)
    monkeypatch.setattr(kalshi_optimize, "calculate_optimal_order_size", lambda *_: 1.0)
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 10.0)
    monkeypatch.setattr(kalshi_optimize, "calculate_edge_pct_with_flag", lambda *_: (10.0, False))
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(kalshi_optimize, "generate_proof", lambda *_args, **_kwargs: None)


def test_dry_run_record_trade_success_does_not_notify_discord(monkeypatch):
    _patch_common(monkeypatch)
    notify = MagicMock()
    record = MagicMock()
    monkeypatch.setattr(kalshi_optimize, "notify_order_placed", notify)
    monkeypatch.setattr(kalshi_optimize, "record_trade", record)

    exit_code, cascade_attempted, total_markets = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
    )

    assert exit_code == 0
    assert cascade_attempted == 1
    assert total_markets == 1
    record.assert_called_once()
    notify.assert_not_called()


def test_dry_run_record_trade_failure_logs_and_does_not_notify_discord(monkeypatch, caplog):
    _patch_common(monkeypatch)
    notify = MagicMock()
    monkeypatch.setattr(kalshi_optimize, "notify_order_placed", notify)
    monkeypatch.setattr(kalshi_optimize, "record_trade", MagicMock(side_effect=RuntimeError("db offline")))

    with caplog.at_level("WARNING"):
        exit_code, _, _ = kalshi_optimize.optimize_kalshi_strategy(
            mode="shadow",
            bankroll=100.0,
            max_pos_usd=10.0,
            dry_run=True,
        )

    assert exit_code == 0
    notify.assert_not_called()
    assert "record_trade failed for dry-run simulated order" in caplog.text
    assert "db offline" in caplog.text


def test_live_successful_order_path_still_notifies_discord(monkeypatch):
    _patch_common(monkeypatch)

    class FakeKalshiOrderClient:
        def get_orders(self, status="resting"):
            return []

        def get_positions(self):
            return []

        def place_order(self, **_kwargs):
            return {"order": {"order_id": "test-order", "taker_fill_cost_dollars": "0.50"}}

    import utils.kalshi as kalshi_api
    import utils.kalshi_orders as kalshi_orders

    notify = MagicMock()
    record = MagicMock()
    monkeypatch.setattr(kalshi_optimize, "notify_order_placed", notify)
    monkeypatch.setattr(kalshi_optimize, "record_trade", record)
    monkeypatch.setattr(kalshi_optimize, "_get_todays_realized_pnl", lambda: 0.0)
    monkeypatch.setattr(kalshi_optimize.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kalshi_api, "get_kalshi_balance", lambda: 10.0)
    monkeypatch.setattr(kalshi_orders, "KalshiOrderClient", FakeKalshiOrderClient)

    exit_code, cascade_attempted, total_markets = kalshi_optimize.optimize_kalshi_strategy(
        mode="real-live",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=False,
    )

    assert exit_code == 0
    assert cascade_attempted == 1
    assert total_markets == 1
    record.assert_called_once()
    notify.assert_called_once()
