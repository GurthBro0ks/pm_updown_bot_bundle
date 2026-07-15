from __future__ import annotations

from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

from research.candidate_ledger import CandidateLedger
from research.candidate_ledger.capture import CaptureConfig
from research.candidate_ledger.runtime_adapter import CandidateCaptureRuntime


RUN_TIMESTAMP = "2026-07-15T20:00:00Z"
GIT_COMMIT = "4d047b9831330eb981946995ec9ec6307768fe1d"


def _market(ticker: str = "KXTEST-26JUL16-T100") -> dict[str, object]:
    return {
        "id": ticker,
        "ticker": ticker,
        "title": "Synthetic public candidate",
        "close_time": "2026-07-16T20:00:00Z",
        "series_ticker": "KXTEST",
        "series_category": "economics",
        "_category": "economics",
        "_ai_tier": "premium",
        "_yes_bid_price": 0.49,
        "_yes_ask_price": 0.51,
    }


def _runtime(database: Path | None, **config_overrides: object) -> CandidateCaptureRuntime:
    config = CaptureConfig(
        enabled=True,
        database_path=str(database) if database is not None else None,
        max_per_run=int(config_overrides.get("max_per_run", 100)),
        sqlite_timeout_ms=int(config_overrides.get("sqlite_timeout_ms", 50)),
        warning_codes=tuple(config_overrides.get("warning_codes", ())),
    )
    return CandidateCaptureRuntime(
        mode="micro-live",
        run_id="synthetic-run-phase1b",
        run_timestamp=RUN_TIMESTAMP,
        git_commit=GIT_COMMIT,
        config=config,
    )


def _record(
    runtime: CandidateCaptureRuntime,
    *,
    market: dict[str, object] | None = None,
    rejection_reason: str | None = None,
    failure_kinds: list[str] | None = None,
    order_intent: bool = False,
) -> bool:
    return runtime.record_evaluation(
        market=market or _market(),
        observed_price=0.50,
        ai_prior=0.70,
        fallback_prior_used="fallback_prior" in (failure_kinds or []),
        raw_edge=40.0,
        fee_adjusted_edge=35.0,
        required_threshold=3.0,
        rejection_reason=rejection_reason,
        gate_failure_kinds=failure_kinds or [],
        order_intent_created=order_intent,
        intent_price=0.49 if order_intent else None,
        maker_assumption="maker" if order_intent else "unknown",
        expected_value=35.0,
    )


def test_disabled_by_default_creates_no_database(tmp_path):
    database = tmp_path / "disabled.sqlite3"
    config = CaptureConfig.from_environment({})
    runtime = CandidateCaptureRuntime(
        mode="micro-live",
        run_id="disabled-run",
        run_timestamp=RUN_TIMESTAMP,
        git_commit=GIT_COMMIT,
        config=config,
    )
    assert _record(runtime) is False
    status = runtime.flush()
    assert status.status == "DISABLED"
    assert database.exists() is False


def test_enable_flag_must_be_exact_true():
    assert CaptureConfig.from_environment({"CANDIDATE_LEDGER_SHADOW_ENABLED": "1"}).enabled is False
    assert CaptureConfig.from_environment({"CANDIDATE_LEDGER_SHADOW_ENABLED": "true"}).enabled is True


def test_enabled_without_database_path_fails_safely():
    config = CaptureConfig.from_environment({"CANDIDATE_LEDGER_SHADOW_ENABLED": "true"})
    runtime = CandidateCaptureRuntime(
        mode="micro-live",
        run_id="missing-path-run",
        run_timestamp=RUN_TIMESTAMP,
        git_commit=GIT_COMMIT,
        config=config,
    )
    assert _record(runtime) is False
    status = runtime.flush()
    assert status.status == "WARN"
    assert status.attempted is False
    assert "database_path_required" in status.warning_codes


def test_enabled_records_accepted_and_rejected_candidates(tmp_path):
    database = tmp_path / "capture.sqlite3"
    runtime = _runtime(database)
    assert _record(runtime, market=_market("KXTEST-REJECT"), rejection_reason="edge_below_threshold", failure_kinds=["edge_below_threshold"])
    assert _record(runtime, market=_market("KXTEST-ACCEPT"), order_intent=True)
    status = runtime.flush()
    assert status.status == "PASS"
    assert status.written_count == 5

    with CandidateLedger.open_read_only(database) as ledger:
        summary = ledger.summary()
        assert summary["event_counts"] == {
            "candidate_observed": 2,
            "gate_evaluated": 2,
            "order_intent_created": 1,
        }
        payloads = list(ledger.candidate_payloads())
        rejected = next(item for item in payloads if item["market"]["ticker"] == "KXTEST-REJECT")
        accepted = next(item for item in payloads if item["market"]["ticker"] == "KXTEST-ACCEPT")
        assert rejected["decision"]["order_intent_created"] is False
        assert accepted["decision"]["order_intent_created"] is True


def test_price_edge_fallback_and_market_end_rejections_are_complete(tmp_path):
    database = tmp_path / "rejections.sqlite3"
    runtime = _runtime(database)
    cases = [
        ("KXPRICE", "price_below_minimum", "price_below_minimum"),
        ("KXEDGE", "edge_below_threshold", "edge_below_threshold"),
        ("KXFALLBACK", "gate_failed", "fallback_prior"),
        ("KXEND", "gate_failed", "market_end_time"),
    ]
    for ticker, reason, kind in cases:
        assert _record(
            runtime,
            market=_market(ticker),
            rejection_reason=reason,
            failure_kinds=[kind],
        )
    assert runtime.flush().status == "PASS"
    with CandidateLedger.open_read_only(database) as ledger:
        assert ledger.summary()["event_counts"].get("order_intent_created", 0) == 0
        for payload in ledger.candidate_payloads():
            assert payload["decision"]["order_intent_created"] is False
            assert payload["gates"]["gate_failure_kinds"]


def test_duplicate_retry_is_idempotent(tmp_path):
    database = tmp_path / "idempotent.sqlite3"
    first = _runtime(database)
    assert _record(first, order_intent=True)
    assert first.flush().written_count == 3
    second = _runtime(database)
    assert _record(second, order_intent=True)
    assert second.flush().written_count == 0
    with CandidateLedger.open_read_only(database) as ledger:
        assert ledger.summary()["event_count"] == 3


def test_database_lock_failure_isolated(tmp_path):
    database = tmp_path / "locked.sqlite3"
    with CandidateLedger.initialize(database):
        pass
    lock = sqlite3.connect(database, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    runtime = _runtime(database, sqlite_timeout_ms=10)
    assert _record(runtime, order_intent=True)
    decision = {"selected": True, "exit_code": 0}
    try:
        status = runtime.flush()
    finally:
        lock.rollback()
        lock.close()
    assert status.status == "WARN"
    assert decision == {"selected": True, "exit_code": 0}


def test_migration_failure_isolated(tmp_path):
    database = tmp_path / "not-ledger.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.commit()
    connection.close()
    runtime = _runtime(database)
    assert _record(runtime)
    status = runtime.flush()
    assert status.status == "WARN"
    assert "database_write_failed" in status.warning_codes


def test_malformed_and_secret_looking_payloads_are_dropped(tmp_path):
    database = tmp_path / "malformed.sqlite3"
    runtime = _runtime(database)
    malformed = _market("KXMALFORMED")
    malformed["title"] = "-----BEGIN PRIVATE KEY----- synthetic fixture"
    assert _record(runtime, market=malformed) is False
    status = runtime.flush()
    assert status.warning_count == 1
    assert database.exists() is False


def test_capture_batch_limit_is_enforced(tmp_path):
    database = tmp_path / "bounded.sqlite3"
    runtime = _runtime(database, max_per_run=1)
    assert _record(runtime, market=_market("KXONE"))
    assert _record(runtime, market=_market("KXTWO")) is False
    status = runtime.flush()
    assert status.written_count == 2
    assert status.dropped_count == 1


def test_raw_prompt_and_secret_fields_are_absent(tmp_path):
    database = tmp_path / "safe.sqlite3"
    runtime = _runtime(database)
    assert _record(runtime, order_intent=True)
    assert runtime.flush().status == "PASS"
    with CandidateLedger.open_read_only(database) as ledger:
        text = "\n".join(str(event.payload) for event in ledger.history(next(iter(ledger.candidate_payloads()))["identity"]["candidate_id"]))
    assert "raw_prompt" not in text
    assert "authorization" not in text.lower()
    assert "webhook" not in text.lower()


def test_new_database_permissions_are_owner_only(tmp_path):
    database = tmp_path / "permissions.sqlite3"
    runtime = _runtime(database)
    assert _record(runtime)
    assert runtime.flush().status == "PASS"
    assert (database.stat().st_mode & 0o777) == 0o600


def test_capture_uses_one_append_batch(tmp_path, monkeypatch):
    database = tmp_path / "batch.sqlite3"
    calls = []
    original = CandidateLedger.append_events

    def tracked(self, events):
        calls.append(len(events))
        return original(self, events)

    monkeypatch.setattr(CandidateLedger, "append_events", tracked)
    runtime = _runtime(database)
    assert _record(runtime, market=_market("KXONE"))
    assert _record(runtime, market=_market("KXTWO"), order_intent=True)
    assert runtime.flush().status == "PASS"
    assert calls == [5]


def test_status_cli_direct_invocation_is_bounded(tmp_path):
    database = tmp_path / "status.sqlite3"
    runtime = _runtime(database)
    assert _record(runtime, order_intent=True)
    assert runtime.flush().status == "PASS"
    result = subprocess.run(
        [sys.executable, "scripts/candidate_capture_status_redacted.py", "--database", str(database)],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "CANDIDATE_CAPTURE_STATUS=PASS" in result.stdout
    assert "CANDIDATE_OBSERVED_COUNT=1" in result.stdout
    assert "GATE_EVALUATED_COUNT=1" in result.stdout
    assert "ORDER_INTENT_COUNT=1" in result.stdout
    assert "RAW_CANDIDATES_INCLUDED=false" in result.stdout
    assert "Synthetic public candidate" not in result.stdout


def test_capture_performs_no_network_call(tmp_path, monkeypatch):
    def forbidden_network(*args, **kwargs):
        raise AssertionError("network use is forbidden")

    monkeypatch.setattr(socket, "socket", forbidden_network)
    runtime = _runtime(tmp_path / "offline.sqlite3")
    assert _record(runtime)
    assert runtime.flush().status == "PASS"
