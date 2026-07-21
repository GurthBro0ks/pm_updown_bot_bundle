from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from research.candidate_ledger import CandidateLedger
from scripts.candidate_capture_spool_ingest import ingest
from strategies import kalshi_optimize


def _market(ticker: str, price: float = 0.50) -> dict[str, object]:
    return {
        "id": ticker,
        "ticker": ticker,
        "title": f"Public title for {ticker}",
        "odds": {"yes": price},
        "volume_24h": 1000,
        "liquidity_usd": 1000,
        "close_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "series_ticker": ticker.split("-")[0],
        "series_category": "index",
        "_yes_bid_price": max(0.01, price - 0.01),
        "_yes_ask_price": min(0.99, price + 0.01),
        "price_history": [],
    }


def _reset_strategy_state() -> None:
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


def _configure_strategy(monkeypatch, tmp_path: Path, markets: list[dict[str, object]]) -> None:
    _reset_strategy_state()
    monkeypatch.setenv("MAIN_EDGE_NEAREST_MISS_PATH", str(tmp_path / "nearest.json"))
    monkeypatch.setattr(kalshi_optimize, "fetch_kalshi_markets", lambda: markets)
    monkeypatch.setattr(kalshi_optimize, "filter_low_liquidity_markets", lambda values, **_: values)
    monkeypatch.setattr(kalshi_optimize, "estimate_true_price", lambda *_, **__: 0.70)
    monkeypatch.setattr(kalshi_optimize, "_apply_vol_model_or_shrinkage", lambda market, probability: probability)
    monkeypatch.setattr(kalshi_optimize, "_was_last_prior_fallback", lambda: False)
    monkeypatch.setattr(kalshi_optimize, "validate_prior", None)
    monkeypatch.setattr(kalshi_optimize, "find_best_maker_market", lambda *_: None)
    monkeypatch.setattr(kalshi_optimize, "calculate_optimal_order_size", lambda *_: 1.0)
    monkeypatch.setattr(kalshi_optimize, "calculate_edge_pct_with_flag", lambda *_: (40.0, False))
    monkeypatch.setattr(kalshi_optimize, "generate_proof", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kalshi_optimize, "record_trade", lambda **_kwargs: None)


def _run(monkeypatch, tmp_path: Path, markets: list[dict[str, object]], database: Path | None):
    _configure_strategy(monkeypatch, tmp_path, markets)
    if database is None:
        monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
        monkeypatch.delenv("CANDIDATE_LEDGER_SPOOL_PATH", raising=False)
    else:
        spool = database.parent / f"{database.stem}-spool"
        spool.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "true")
        monkeypatch.setenv("CANDIDATE_LEDGER_SPOOL_PATH", str(spool))
        monkeypatch.setenv("CANDIDATE_LEDGER_CAPTURE_MAX_PER_RUN", "20")
    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
    )
    if database is not None:
        ingest(spool, database, timeout_ms=20)
    return result


def test_pipeline_captures_edge_rejection_and_existing_order_intent(monkeypatch, tmp_path):
    database = tmp_path / "pipeline.sqlite3"
    markets = [_market("KXEDGE-ONE"), _market("KXACCEPT-TWO")]
    monkeypatch.setattr(
        kalshi_optimize,
        "get_edge_after_fees",
        lambda market, **_: 1.0 if market["id"] == "KXEDGE-ONE" else 15.0,
    )
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    result = _run(monkeypatch, tmp_path, markets, database)
    assert result == (0, 2, 2)
    redacted_summary = json.loads((tmp_path / "nearest.json").read_text())
    assert redacted_summary["SHADOW_CAPTURE_ENABLED"] == "true"
    assert redacted_summary["SHADOW_CAPTURE_STATUS"] == "PASS"
    assert redacted_summary["SHADOW_CAPTURE_WRITTEN_COUNT"] == 5
    assert redacted_summary["CANDIDATE_OBSERVED_COUNT"] == 2
    assert redacted_summary["GATE_EVALUATED_COUNT"] == 2
    assert redacted_summary["ORDER_INTENT_COUNT"] == 1
    assert redacted_summary["ORDER_ATTEMPT_COUNT"] == 0
    assert redacted_summary["ORDER_RESULT_COUNT"] == 0
    with CandidateLedger.open_read_only(database) as ledger:
        summary = ledger.summary()
        assert summary["event_counts"] == {
            "candidate_observed": 2,
            "gate_evaluated": 2,
            "order_intent_created": 1,
        }
        payloads = {item["market"]["ticker"]: item for item in ledger.candidate_payloads()}
        assert payloads["KXEDGE-ONE"]["decision"]["rejection_reason"] == "edge_below_threshold"
        assert payloads["KXACCEPT-TWO"]["decision"]["order_intent_created"] is True


def test_pipeline_price_floor_rejection_has_no_order_intent(monkeypatch, tmp_path):
    database = tmp_path / "price.sqlite3"
    monkeypatch.setattr(kalshi_optimize, "MIN_TRADE_PRICE_CENTS", 25)
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 50.0)
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    result = _run(monkeypatch, tmp_path, [_market("KXPRICE-ONE", price=0.10)], database)
    assert result == (0, 1, 1)
    with CandidateLedger.open_read_only(database) as ledger:
        payload = next(ledger.candidate_payloads())
        assert payload["decision"]["rejection_reason"] == "price_below_minimum"
        assert ledger.summary()["event_counts"].get("order_intent_created", 0) == 0


@pytest.mark.parametrize(
    ("violation", "expected_kind"),
    [
        ("fallback prior (cascade failed, no real AI view)", "fallback_prior"),
        ("Market ends in 0.5h < min 24h", "market_end_time"),
    ],
)
def test_pipeline_gate_rejections_are_captured(monkeypatch, tmp_path, violation, expected_kind):
    database = tmp_path / f"{expected_kind}.sqlite3"
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 15.0)
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (False, [violation]))
    result = _run(monkeypatch, tmp_path, [_market(f"KX{expected_kind.upper()}-ONE")], database)
    assert result == (0, 1, 1)
    with CandidateLedger.open_read_only(database) as ledger:
        payload = next(ledger.candidate_payloads())
        assert expected_kind in payload["gates"]["gate_failure_kinds"]
        assert payload["decision"]["order_intent_created"] is False


def test_disabled_and_enabled_decisions_sizing_intents_and_exit_are_equivalent(monkeypatch, tmp_path):
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 15.0)
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    disabled = _run(monkeypatch, tmp_path / "disabled", [_market("KXEQUIV-ONE")], None)
    enabled_db = tmp_path / "enabled.sqlite3"
    enabled = _run(monkeypatch, tmp_path / "enabled", [_market("KXEQUIV-ONE")], enabled_db)
    assert disabled == enabled == (0, 1, 1)
    with CandidateLedger.open_read_only(enabled_db) as ledger:
        candidate = next(ledger.candidate_payloads())
        assert candidate["decision"]["order_intent_created"] is True
        assert candidate["decision"]["quantity"] == 1


def test_capture_migration_failure_does_not_change_strategy_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 15.0)
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    disabled = _run(monkeypatch, tmp_path / "disabled", [_market("KXFAIL-ONE")], None)
    bad_database = tmp_path / "bad.sqlite3"
    connection = sqlite3.connect(bad_database)
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    connection.close()
    failed_capture = _run(monkeypatch, tmp_path / "failed", [_market("KXFAIL-ONE")], bad_database)
    assert disabled == failed_capture == (0, 1, 1)


def test_capture_busy_failure_does_not_change_strategy_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 15.0)
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    disabled = _run(monkeypatch, tmp_path / "disabled", [_market("KXBUSY-ONE")], None)
    database = tmp_path / "busy.sqlite3"
    with CandidateLedger.initialize(database):
        pass
    lock = sqlite3.connect(database, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        busy_capture = _run(monkeypatch, tmp_path / "busy", [_market("KXBUSY-ONE")], database)
    finally:
        lock.rollback()
        lock.close()
    assert disabled == busy_capture == (0, 1, 1)


def test_runtime_spool_failure_does_not_change_strategy_exit(monkeypatch, tmp_path):
    market = _market("KXSPOOL-FAIL")
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 15.0)
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    disabled = _run(monkeypatch, tmp_path / "disabled", [market], None)

    failed_path = tmp_path / "failed"
    _configure_strategy(monkeypatch, failed_path, [_market("KXSPOOL-FAIL")])
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_LEDGER_SPOOL_PATH", str(tmp_path / "missing-spool"))
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", lambda *_args, **_kwargs: 15.0)
    monkeypatch.setattr(kalshi_optimize, "check_micro_live_gates", lambda *_args, **_kwargs: (True, []))
    failed = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow", bankroll=100.0, max_pos_usd=10.0, dry_run=True
    )
    assert disabled == failed == (0, 1, 1)


def _failed_prior_validation() -> dict[str, object]:
    return {
        "passed": False,
        "adjusted_prior": 0.70,
        "confidence": 0.0,
        "flags": ["synthetic"],
        "reason": "synthetic prior rejection",
    }


def test_prior_failure_helpers_stay_behind_nearest_miss_guard(monkeypatch, tmp_path):
    market = _market("KXPRIOR-GUARD-FALSE")
    _configure_strategy(monkeypatch, tmp_path, [market])
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.delenv("CANDIDATE_LEDGER_SPOOL_PATH", raising=False)
    monkeypatch.setattr(kalshi_optimize, "validate_prior", lambda **_: _failed_prior_validation())
    monkeypatch.setattr(kalshi_optimize, "make_nearest_miss", None)

    def unexpected_helper(*_args, **_kwargs):
        raise AssertionError("guarded edge helper was invoked")

    monkeypatch.setattr(kalshi_optimize, "calculate_edge_pct", unexpected_helper)
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", unexpected_helper)

    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
    )

    assert result == (0, 1, 1)
    assert not (tmp_path / "candidate-ledger.sqlite3").exists()
    summary = json.loads((tmp_path / "nearest.json").read_text())
    assert summary["SHADOW_CAPTURE_ENABLED"] == "false"
    assert summary["SHADOW_CAPTURE_STATUS"] == "DISABLED"


def test_prior_failure_helpers_run_inside_true_guard_without_decision_change(monkeypatch, tmp_path):
    market = _market("KXPRIOR-GUARD-TRUE")
    _configure_strategy(monkeypatch, tmp_path, [market])
    database = tmp_path / "candidate-ledger.sqlite3"
    spool = tmp_path / "candidate-spool"
    spool.mkdir()
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_LEDGER_SPOOL_PATH", str(spool))
    monkeypatch.setattr(kalshi_optimize, "validate_prior", lambda **_: _failed_prior_validation())
    calls = {"raw": 0, "fee": 0, "nearest": 0}
    original_nearest = kalshi_optimize.make_nearest_miss

    def raw_edge(*_args, **_kwargs):
        calls["raw"] += 1
        return 40.0

    def fee_edge(*_args, **_kwargs):
        calls["fee"] += 1
        return 35.0

    def nearest(**kwargs):
        calls["nearest"] += 1
        return original_nearest(**kwargs)

    monkeypatch.setattr(kalshi_optimize, "calculate_edge_pct", raw_edge)
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", fee_edge)
    monkeypatch.setattr(kalshi_optimize, "make_nearest_miss", nearest)

    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
    )

    assert result == (0, 1, 1)
    assert calls == {"raw": 1, "fee": 1, "nearest": 1}
    summary = json.loads((tmp_path / "nearest.json").read_text())
    assert summary["SHADOW_CAPTURE_ENABLED"] == "true"
    assert summary["SHADOW_CAPTURE_STATUS"] == "PASS"
    assert ingest(spool, database)[0]["CANDIDATE_SPOOL_INGEST"] == "PASS"
    with CandidateLedger.open_read_only(database) as ledger:
        candidate = next(ledger.candidate_payloads())
        assert candidate["decision"]["rejection_reason"] == "prior_validation_failed"
        assert candidate["decision"]["order_intent_created"] is False


def test_prior_failure_guard_false_still_captures_complete_rejection(monkeypatch, tmp_path):
    market = _market("KXPRIOR-COMPLETE")
    _configure_strategy(monkeypatch, tmp_path, [market])
    spool = tmp_path / "candidate-spool"
    spool.mkdir()
    database = tmp_path / "candidate-ledger.sqlite3"
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_LEDGER_SPOOL_PATH", str(spool))
    monkeypatch.setattr(kalshi_optimize, "validate_prior", lambda **_: _failed_prior_validation())
    monkeypatch.setattr(kalshi_optimize, "make_nearest_miss", None)

    def unexpected_helper(*_args, **_kwargs):
        raise AssertionError("guarded edge helper was invoked")

    monkeypatch.setattr(kalshi_optimize, "calculate_edge_pct", unexpected_helper)
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", unexpected_helper)
    enabled = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow", bankroll=100.0, max_pos_usd=10.0, dry_run=True
    )
    assert enabled == (0, 1, 1)
    assert ingest(spool, database)[0]["CANDIDATE_SPOOL_INGEST"] == "PASS"
    with CandidateLedger.open_read_only(database) as ledger:
        counts = ledger.summary()["event_counts"]
        candidate = next(ledger.candidate_payloads())
    assert counts == {"candidate_observed": 1, "gate_evaluated": 1}
    assert candidate["decision"]["rejection_reason"] == "prior_validation_failed"
    assert candidate["decision"]["order_intent_created"] is False

    disabled_path = tmp_path / "disabled-equivalence"
    _configure_strategy(monkeypatch, disabled_path, [_market("KXPRIOR-COMPLETE")])
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.delenv("CANDIDATE_LEDGER_SPOOL_PATH", raising=False)
    monkeypatch.setattr(kalshi_optimize, "validate_prior", lambda **_: _failed_prior_validation())
    monkeypatch.setattr(kalshi_optimize, "make_nearest_miss", None)
    monkeypatch.setattr(kalshi_optimize, "calculate_edge_pct", unexpected_helper)
    monkeypatch.setattr(kalshi_optimize, "get_edge_after_fees", unexpected_helper)
    disabled = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow", bankroll=100.0, max_pos_usd=10.0, dry_run=True
    )
    assert disabled == enabled
