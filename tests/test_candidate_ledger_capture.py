from __future__ import annotations

import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

from research.candidate_ledger import CandidateLedger
from research.candidate_ledger.capture import CaptureConfig
from research.candidate_ledger.runtime_adapter import CandidateCaptureRuntime
from research.candidate_ledger.spool import (
    SPOOL_SUFFIX,
    SPOOL_VERSION,
    SpoolError,
    parse_batch_bytes,
    read_batch,
    serialize_batch,
)
from scripts.candidate_capture_spool_ingest import ingest


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


def _runtime(spool: Path | None, **overrides: object) -> CandidateCaptureRuntime:
    config = CaptureConfig(
        enabled=True,
        spool_path=str(spool) if spool is not None else None,
        max_per_run=int(overrides.get("max_per_run", 100)),
        max_events_per_batch=int(overrides.get("max_events_per_batch", 300)),
        max_batch_bytes=int(overrides.get("max_batch_bytes", 2 * 1024 * 1024)),
        max_spool_bytes=int(overrides.get("max_spool_bytes", 256 * 1024 * 1024)),
        max_spool_batches=int(overrides.get("max_spool_batches", 10_000)),
        warning_codes=tuple(overrides.get("warning_codes", ())),
    )
    return CandidateCaptureRuntime(
        mode="micro-live",
        run_id=str(overrides.get("run_id", "synthetic-run-phase1b")),
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


def _spool(tmp_path: Path, name: str = "spool") -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def _ingest(spool: Path, database: Path, **kwargs: object) -> dict[str, object]:
    fields, _ = ingest(spool, database, **kwargs)
    return fields


def test_disabled_by_default_creates_no_directory_file_or_database(tmp_path):
    runtime = CandidateCaptureRuntime(
        mode="micro-live",
        run_id="disabled-run",
        run_timestamp=RUN_TIMESTAMP,
        git_commit=GIT_COMMIT,
        config=CaptureConfig.from_environment({}),
    )
    assert _record(runtime) is False
    assert runtime.flush().status == "DISABLED"
    assert list(tmp_path.iterdir()) == []


def test_enable_flag_and_explicit_spool_path_are_required():
    assert CaptureConfig.from_environment({"CANDIDATE_LEDGER_SHADOW_ENABLED": "1"}).enabled is False
    config = CaptureConfig.from_environment({"CANDIDATE_LEDGER_SHADOW_ENABLED": "true"})
    assert config.enabled is True
    assert "spool_path_required" in config.warning_codes
    runtime = _runtime(None, warning_codes=config.warning_codes)
    assert _record(runtime) is False
    assert runtime.flush().status == "WARN"


def test_runtime_writes_exactly_one_bounded_batch_and_never_opens_sqlite(tmp_path, monkeypatch):
    spool = _spool(tmp_path)

    def forbidden_sqlite(*_args, **_kwargs):
        raise AssertionError("runtime capture opened SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden_sqlite)
    runtime = _runtime(spool)
    assert _record(runtime, market=_market("KX-REJECT"), rejection_reason="edge_below_threshold", failure_kinds=["edge_below_threshold"])
    assert _record(runtime, market=_market("KX-ACCEPT"), order_intent=True)
    status = runtime.flush()
    assert status.status == "PASS"
    assert status.batch_written is True
    assert status.written_count == 5
    batches = list(spool.glob(f"*{SPOOL_SUFFIX}"))
    assert len(batches) == 1
    envelope = read_batch(batches[0])
    assert envelope["event_count"] == 5
    assert envelope["body"]["spool_version"] == SPOOL_VERSION


def test_runtime_retry_and_offline_ingest_retry_are_idempotent(tmp_path):
    spool = _spool(tmp_path)
    database = tmp_path / "ledger.sqlite3"
    first = _runtime(spool)
    assert _record(first, order_intent=True)
    assert first.flush().written_count == 3
    second = _runtime(spool)
    assert _record(second, order_intent=True)
    retry = second.flush()
    assert retry.status == "PASS" and retry.written_count == 0
    first_ingest = _ingest(spool, database)
    second_ingest = _ingest(spool, database)
    assert first_ingest["BATCHES_INGESTED"] == 1
    assert first_ingest["EVENTS_INGESTED"] == 3
    assert second_ingest["BATCHES_ALREADY_PRESENT"] == 1
    assert second_ingest["EVENTS_INGESTED"] == 0
    with CandidateLedger.open_read_only(database) as ledger:
        assert ledger.summary()["event_count"] == 3
    with CandidateLedger(database) as ledger:
        for statement in (
            "UPDATE candidate_spool_batches SET event_count=event_count",
            "DELETE FROM candidate_spool_batches",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                ledger.connection.execute(statement)


def test_rejections_are_complete_and_have_no_order_events(tmp_path):
    spool = _spool(tmp_path)
    database = tmp_path / "ledger.sqlite3"
    runtime = _runtime(spool)
    for ticker, reason, kind in (
        ("KXPRICE", "price_below_minimum", "price_below_minimum"),
        ("KXEDGE", "edge_below_threshold", "edge_below_threshold"),
        ("KXFALLBACK", "gate_failed", "fallback_prior"),
        ("KXEND", "gate_failed", "market_end_time"),
    ):
        assert _record(runtime, market=_market(ticker), rejection_reason=reason, failure_kinds=[kind])
    assert runtime.flush().status == "PASS"
    assert _ingest(spool, database)["CANDIDATE_SPOOL_INGEST"] == "PASS"
    with CandidateLedger.open_read_only(database) as ledger:
        counts = ledger.summary()["event_counts"]
        assert counts == {"candidate_observed": 4, "gate_evaluated": 4}


def test_runtime_failures_are_isolated_and_truthfully_counted(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    runtime = _runtime(missing)
    assert _record(runtime, order_intent=True)
    decision = {"selected": True, "quantity": 1, "exit_code": 0}
    status = runtime.flush()
    assert status.status == "WARN"
    assert status.dropped_count == 1
    assert "spool_write_failed" in status.warning_codes
    assert decision == {"selected": True, "quantity": 1, "exit_code": 0}

    spool = _spool(tmp_path)
    runtime = _runtime(spool, max_spool_bytes=4096)
    assert _record(runtime)
    monkeypatch.setattr("research.candidate_ledger.capture.write_batch", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("synthetic unwritable")))
    assert runtime.flush().status == "WARN"


def test_spool_capacity_and_candidate_limits_are_enforced(tmp_path):
    spool = _spool(tmp_path)
    runtime = _runtime(spool, max_per_run=1)
    assert _record(runtime, market=_market("KXONE"))
    assert _record(runtime, market=_market("KXTWO")) is False
    status = runtime.flush()
    assert status.written_count == 2 and status.dropped_count == 1

    full = _spool(tmp_path, "full")
    capped = _runtime(full, max_spool_batches=0)
    assert _record(capped)
    assert capped.flush().status == "WARN"

    byte_limited = _spool(tmp_path, "byte-limited")
    first = _runtime(byte_limited, run_id="first")
    assert _record(first)
    assert first.flush().status == "PASS"
    current_bytes = sum(path.stat().st_size for path in byte_limited.glob(f"*{SPOOL_SUFFIX}"))
    second = _runtime(byte_limited, run_id="second", max_spool_bytes=current_bytes)
    assert _record(second, market=_market("KXSECOND"))
    second_status = second.flush()
    assert second_status.status == "WARN"
    assert "spool_capacity_reached" in second_status.warning_codes


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data[:-3],
        lambda data: data.replace(b"candidate-capture-spool.v1", b"candidate-capture-spool.v9"),
        lambda data: data.replace(b'"body_sha256":"', b'"body_sha256":"0', 1),
        lambda _data: b"not-json\n",
    ],
)
def test_partial_version_checksum_and_malformed_records_are_detected(tmp_path, mutation):
    spool = _spool(tmp_path)
    runtime = _runtime(spool)
    assert _record(runtime)
    assert runtime.flush().status == "PASS"
    source = next(spool.glob(f"*{SPOOL_SUFFIX}"))
    corrupted = mutation(source.read_bytes())
    with pytest.raises(SpoolError):
        parse_batch_bytes(corrupted)


def test_offline_ingest_conflict_is_atomic_and_spool_is_retained(tmp_path):
    spool = _spool(tmp_path)
    database = tmp_path / "ledger.sqlite3"
    runtime = _runtime(spool)
    assert _record(runtime)
    assert runtime.flush().status == "PASS"
    assert _ingest(spool, database)["BATCHES_INGESTED"] == 1
    original_path = next(spool.glob(f"*{SPOOL_SUFFIX}"))
    envelope = read_batch(original_path)
    gate = dict(envelope["body"]["events"][1])
    gate["payload"] = json.loads(json.dumps(gate["payload"]))
    gate["payload"]["decision"]["rejection_reason"] = "synthetic_conflict"
    _, serialized = serialize_batch([gate])
    conflict_id = json.loads(serialized)["batch_id"]
    (spool / f"{conflict_id}{SPOOL_SUFFIX}").write_bytes(serialized)
    result = _ingest(spool, database)
    assert result["CANDIDATE_SPOOL_INGEST"] == "WARN"
    assert result["BATCHES_FAILED"] == 1
    assert original_path.exists()
    with CandidateLedger.open_read_only(database) as ledger:
        assert ledger.summary()["event_count"] == 2


def test_sqlite_lock_and_future_migration_fail_offline_without_spool_loss(tmp_path):
    spool = _spool(tmp_path)
    runtime = _runtime(spool)
    assert _record(runtime)
    assert runtime.flush().status == "PASS"
    batch = next(spool.glob(f"*{SPOOL_SUFFIX}"))
    database = tmp_path / "locked.sqlite3"
    with CandidateLedger.initialize(database):
        pass
    lock = sqlite3.connect(database, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        fields, code = ingest(spool, database, timeout_ms=10)
    finally:
        lock.rollback()
        lock.close()
    assert code in {1, 2} and fields["BATCHES_FAILED"] == 1 and batch.exists()

    with CandidateLedger(database) as ledger:
        ledger.connection.execute(
            "INSERT INTO schema_migrations(version,applied_at) VALUES (999,'2026-07-16T00:00:00Z')"
        )
    fields, code = ingest(spool, database)
    assert code == 2 and fields["CANDIDATE_SPOOL_INGEST"] == "FAIL" and batch.exists()


def test_secret_material_is_rejected_and_spool_files_are_owner_only(tmp_path):
    spool = _spool(tmp_path)
    runtime = _runtime(spool)
    malformed = _market("KXMALFORMED")
    malformed["title"] = "-----BEGIN PRIVATE KEY----- synthetic fixture"
    assert _record(runtime, market=malformed) is False
    assert runtime.flush().warning_count == 1
    assert list(spool.glob(f"*{SPOOL_SUFFIX}")) == []

    clean = _runtime(spool, run_id="clean")
    assert _record(clean, order_intent=True)
    assert clean.flush().status == "PASS"
    batch = next(spool.glob(f"*{SPOOL_SUFFIX}"))
    text = batch.read_text()
    assert (batch.stat().st_mode & 0o777) == 0o600
    for forbidden in ("raw_prompt", "authorization", "webhook", "private_key"):
        assert forbidden not in text.lower()


def test_status_and_ingest_clis_are_direct_bounded_and_redacted(tmp_path):
    spool = _spool(tmp_path)
    database = tmp_path / "status.sqlite3"
    runtime = _runtime(spool)
    assert _record(runtime, order_intent=True)
    assert runtime.flush().status == "PASS"
    assert _ingest(spool, database)["CANDIDATE_SPOOL_INGEST"] == "PASS"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/candidate_capture_status_redacted.py", "--spool", str(spool), "--database", str(database)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for field in (
        "RUNTIME_CAPTURE_MODE=disabled",
        "SPOOL_BATCH_COUNT=1",
        "SPOOL_PENDING_BATCH_COUNT=0",
        "LEDGER_EVENT_COUNT=3",
        "RAW_CANDIDATES_INCLUDED=false",
        "VALUES_PRINTED=no_secret_values",
    ):
        assert field in result.stdout
    assert "Synthetic public candidate" not in result.stdout

    dry_run_database = tmp_path / "dry-run.sqlite3"
    dry = subprocess.run(
        [sys.executable, "scripts/candidate_capture_spool_ingest.py", "--spool", str(spool), "--database", str(dry_run_database), "--dry-run"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert dry.returncode == 0
    assert "CANDIDATE_SPOOL_INGEST=PASS" in dry.stdout
    assert dry_run_database.exists() is False

    missing = tmp_path / "missing-ledger.sqlite3"
    failed_status = subprocess.run(
        [sys.executable, "scripts/candidate_capture_status_redacted.py", "--database", str(missing)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed_status.returncode == 1
    assert "CANDIDATE_CAPTURE_STATUS=WARN" in failed_status.stdout
    assert "LEDGER_EXISTS=no" in failed_status.stdout
    assert missing.exists() is False


def test_capture_and_ingest_perform_no_network_call(tmp_path, monkeypatch):
    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("network use is forbidden")

    monkeypatch.setattr(socket, "socket", forbidden_network)
    spool = _spool(tmp_path)
    runtime = _runtime(spool)
    assert _record(runtime)
    assert runtime.flush().status == "PASS"
    assert _ingest(spool, tmp_path / "offline.sqlite3")["CANDIDATE_SPOOL_INGEST"] == "PASS"
