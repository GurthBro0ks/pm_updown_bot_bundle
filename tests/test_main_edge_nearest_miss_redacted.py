import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from scripts import main_edge_nearest_miss_redacted as cli
from strategies import kalshi_optimize
from utils import edge_nearest_miss as diag


def _market(ticker="KXINXU-26JUL09H1600-T29319.99", category="index"):
    close_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return {
        "id": ticker,
        "ticker": ticker,
        "title": "Public market title",
        "odds": {"yes": 0.50},
        "volume_24h": 1000,
        "liquidity_usd": 1000,
        "close_time": close_time,
        "series_category": category,
        "_category": category,
        "_ai_tier": "premium",
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


def test_nearest_miss_output_includes_public_edge_fields(tmp_path):
    miss = diag.make_nearest_miss(
        market=_market(),
        side="yes",
        price=0.50,
        ai_prior=0.51,
        raw_edge=2.0,
        fee_adjusted_edge=-4.67,
        required_threshold=3.0,
        rejection_reason="edge_below_threshold",
    )
    summary = diag.build_summary(
        run_timestamp="2026-07-09T12:00:20Z",
        ai_processed_count=20,
        order_intent_count=0,
        nearest_misses=[miss],
        edge_threshold=3.0,
        fee_adjusted_edge_threshold=3.0,
        no_profitable_maker_count=1,
        price_gate_blocked_count=0,
        edge_or_profitability_blocked_count=2,
        order_placed_count=0,
    )
    path = tmp_path / "summary.json"
    diag.write_summary(summary, path)

    output = diag.format_summary(diag.load_summary(path))

    assert "MAIN_EDGE_NEAREST_MISS=PASS" in output
    assert "AI_PROCESSED_COUNT=20" in output
    assert "ticker:KXINXU-26JUL09H1600-T29319.99" in output
    assert "category:index" in output
    assert "price_cents:50" in output
    assert "ai_prior:0.51" in output
    assert "raw_edge:2.0" in output
    assert "fee_adjusted_edge:-4.67" in output
    assert "required_threshold:3.0" in output
    assert "rejection_reason:edge_below_threshold" in output
    assert "VALUES_PRINTED=no_secret_values" in output


def test_secret_looking_fake_values_are_redacted_or_absent(tmp_path):
    miss = diag.make_nearest_miss(
        market=_market(ticker="authorization-bearer-fake", category="api_key_secret_fake"),
        side="yes",
        price=0.42,
        ai_prior=0.43,
        raw_edge=1.0,
        fee_adjusted_edge=-5.5,
        required_threshold=3.0,
        rejection_reason="password_token_should_not_print",
    )
    summary = diag.build_summary(
        run_timestamp="2026-07-09T12:00:20Z",
        ai_processed_count=1,
        order_intent_count=0,
        nearest_misses=[miss],
        edge_threshold=3.0,
        fee_adjusted_edge_threshold=3.0,
        no_profitable_maker_count=0,
        price_gate_blocked_count=0,
        edge_or_profitability_blocked_count=1,
        order_placed_count=0,
    )
    path = tmp_path / "summary.json"
    diag.write_summary(summary, path)
    text = path.read_text()
    output = diag.format_summary(diag.load_summary(path))

    assert "authorization-bearer-fake" not in text
    assert "api_key_secret_fake" not in text
    assert "password_token_should_not_print" not in text
    assert "authorization-bearer-fake" not in output
    assert "api_key_secret_fake" not in output
    assert "password_token_should_not_print" not in output
    assert "redacted" in output


def test_bounded_sample_limit_is_enforced():
    misses = [
        diag.make_nearest_miss(
            market=_market(ticker=f"KXTEST-{idx}", category="index"),
            side="yes",
            price=0.50,
            ai_prior=0.50 + (idx / 1000),
            raw_edge=float(idx),
            fee_adjusted_edge=float(idx),
            required_threshold=10.0,
            rejection_reason="edge_below_threshold",
        )
        for idx in range(12)
    ]

    summary = diag.build_summary(
        run_timestamp="2026-07-09T12:00:20Z",
        ai_processed_count=12,
        order_intent_count=0,
        nearest_misses=misses,
        edge_threshold=10.0,
        fee_adjusted_edge_threshold=10.0,
        no_profitable_maker_count=1,
        price_gate_blocked_count=0,
        edge_or_profitability_blocked_count=12,
        order_placed_count=0,
        sample_limit=3,
    )

    assert summary["nearest_miss_count"] == 12
    assert len(summary["nearest_misses"]) == 3


def test_missing_latest_run_data_returns_warn(tmp_path, capsys):
    missing = tmp_path / "missing.json"

    exit_code = cli.main(["--summary", str(missing)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "MAIN_EDGE_NEAREST_MISS=WARN_NO_PRIOR_STRUCTURED_DETAIL" in output
    assert "ZERO_ORDER_REASON=future_cron_run_required_for_structured_detail" in output


def test_direct_invocation_works_without_pythonpath(tmp_path):
    script = Path("scripts/main_edge_nearest_miss_redacted.py").resolve()
    missing = tmp_path / "missing.json"

    result = subprocess.run(
        [sys.executable, str(script), "--summary", str(missing)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "MAIN_EDGE_NEAREST_MISS=WARN_NO_PRIOR_STRUCTURED_DETAIL" in result.stdout


def test_edge_blocked_strategy_path_writes_summary_and_does_not_place_order(monkeypatch, tmp_path):
    _reset_strategy_state()
    summary_path = tmp_path / "nearest.json"
    market = _market()
    place_order = MagicMock()

    class FakeKalshiOrderClient:
        def get_orders(self, status="resting"):
            return []

        def get_positions(self):
            return []

        def place_order(self, **kwargs):
            return place_order(**kwargs)

    import utils.kalshi_orders as kalshi_orders

    monkeypatch.setenv("MAIN_EDGE_NEAREST_MISS_PATH", str(summary_path))
    monkeypatch.setattr(kalshi_optimize, "fetch_kalshi_markets", lambda: [market])
    monkeypatch.setattr(kalshi_optimize, "filter_low_liquidity_markets", lambda markets, **_: markets)
    monkeypatch.setattr(kalshi_optimize, "estimate_true_price", lambda *_, **__: 0.51)
    monkeypatch.setattr(kalshi_optimize, "_apply_vol_model_or_shrinkage", lambda market, prob: prob)
    monkeypatch.setattr(kalshi_optimize, "_was_last_prior_fallback", None)
    monkeypatch.setattr(kalshi_optimize, "validate_prior", None)
    monkeypatch.setattr(kalshi_optimize, "find_best_maker_market", lambda *_: None)
    monkeypatch.setattr(kalshi_optimize, "calculate_optimal_order_size", lambda *_: 1.0)
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 1.0)
    monkeypatch.setattr(kalshi_optimize, "calculate_edge_pct_with_flag", lambda *_: (2.0, False))
    monkeypatch.setattr(kalshi_optimize, "generate_proof", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kalshi_orders, "KalshiOrderClient", FakeKalshiOrderClient)

    exit_code, ai_processed, total_markets = kalshi_optimize.optimize_kalshi_strategy(
        mode="real-live",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=False,
    )

    assert exit_code == 0
    assert ai_processed == 1
    assert total_markets == 1
    place_order.assert_not_called()

    summary = json.loads(summary_path.read_text())
    assert summary["ai_processed_count"] == 1
    assert summary["order_intent_count"] == 0
    assert summary["nearest_miss_count"] == 1
    assert summary["nearest_misses"][0]["rejection_reason"] == "edge_below_threshold"
    assert summary["values_printed"] == "no_secret_values"
