from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from research.candidate_ledger.models import canonical_json, sha256_text
from research.candidate_ledger.store import CandidateLedger
import research.candidate_ledger.store as store_module


ROOT = Path(__file__).resolve().parents[1]
STATUS_CLI = ROOT / "scripts" / "candidate_capture_status_redacted.py"
VALIDATE_CLI = ROOT / "scripts" / "candidate_ledger_validate.py"


def _candidate_events(candidate_factory, count: int, *, start: int = 0):
    events = []
    for index in range(start, start + count):
        candidate = candidate_factory(
            identity__run_id=f"quick-run-{index:06d}",
            market__ticker=f"SYNTHETIC-QUICK-{index:06d}",
        )
        events.append(
            {
                "candidate_id": candidate["identity"]["candidate_id"],
                "event_type": "candidate_observed",
                "event_timestamp": "2026-07-16T00:00:00Z",
                "payload": candidate,
            }
        )
    return events


def _insert_old_structural_rows(ledger: CandidateLedger, count: int) -> None:
    ledger.connection.execute(
        "WITH RECURSIVE numbers(value) AS ("
        "SELECT 1 UNION ALL SELECT value + 1 FROM numbers WHERE value < ?"
        ") INSERT INTO candidate_events("
        "event_id,candidate_id,event_type,event_timestamp,schema_version,"
        "payload_json,payload_sha256,created_at) "
        "SELECT printf('evt_old_%08d', value), printf('cand_old_%08d', value), "
        "'candidate_observed','2025-01-01T00:00:00Z','1.0.0','{}','synthetic',"
        "'2025-01-01T00:00:00Z' FROM numbers",
        (count,),
    )


def test_status_query_plans_use_migration_index_without_temporary_sort(tmp_path):
    database = tmp_path / "plans.sqlite3"
    with CandidateLedger.initialize(database) as ledger:
        plans = {
            "counts": [
                row[3]
                for row in ledger.connection.execute(
                    "EXPLAIN QUERY PLAN SELECT event_type,COUNT(*) AS count "
                    "FROM candidate_events GROUP BY event_type ORDER BY event_type"
                )
            ],
            "latest": [
                row[3]
                for row in ledger.connection.execute(
                    "EXPLAIN QUERY PLAN SELECT payload_json FROM candidate_events "
                    "WHERE event_type='candidate_observed' "
                    "ORDER BY event_timestamp DESC,sequence DESC LIMIT 1"
                )
            ],
        }
    assert any("candidate_event_type_time" in detail for detail in plans["counts"])
    assert any("candidate_event_type_time" in detail for detail in plans["latest"])
    assert all("TEMP B-TREE" not in detail for details in plans.values() for detail in details)


def test_quick_validation_is_truthful_and_does_not_decode_full_history(
    tmp_path, candidate_factory, monkeypatch
):
    database = tmp_path / "quick.sqlite3"
    with CandidateLedger.initialize(database) as ledger:
        _insert_old_structural_rows(ledger, 250)
        ledger.append_events(_candidate_events(candidate_factory, 101))

    calls = 0
    original_loads = store_module.json.loads

    def tracked_loads(value):
        nonlocal calls
        calls += 1
        return original_loads(value)

    monkeypatch.setattr(store_module.json, "loads", tracked_loads)
    with CandidateLedger.open_read_only(database) as ledger:
        result = ledger.validate_quick()
    assert result["valid"] is True
    assert result["validation_mode"] == "quick"
    assert result["historical_payloads_fully_validated"] is False
    assert result["events_checked"] == 100
    assert calls == 100


def test_deep_validation_detects_malformed_old_event_ignored_by_bounded_window(
    tmp_path, candidate_factory
):
    database = tmp_path / "old-malformed.sqlite3"
    with CandidateLedger.initialize(database) as ledger:
        ledger.connection.execute(
            "INSERT INTO candidate_events("
            "event_id,candidate_id,event_type,event_timestamp,schema_version,"
            "payload_json,payload_sha256,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "evt_malformed_old",
                "cand_malformed_old",
                "candidate_observed",
                "2025-01-01T00:00:00Z",
                "1.0.0",
                "{",
                "synthetic",
                "2025-01-01T00:00:00Z",
            ),
        )
        ledger.append_events(_candidate_events(candidate_factory, 101))
    with CandidateLedger.open_read_only(database) as ledger:
        quick = ledger.validate_quick()
        deep = ledger.validate_deep()
    assert quick["valid"] is True
    assert quick["historical_payloads_fully_validated"] is False
    assert deep["valid"] is False
    assert deep["historical_payloads_fully_validated"] is True
    assert deep["events_checked"] == 102
    assert any("sequence 1" in error for error in deep["errors"])


def test_quick_and_deep_cli_contracts_are_explicit(tmp_path, candidate_factory):
    database = tmp_path / "cli.sqlite3"
    with CandidateLedger.initialize(database) as ledger:
        ledger.append_events(_candidate_events(candidate_factory, 2))
    quick = subprocess.run(
        [sys.executable, str(VALIDATE_CLI), "--database", str(database)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    deep = subprocess.run(
        [sys.executable, str(VALIDATE_CLI), "--database", str(database), "--deep"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert quick.returncode == deep.returncode == 0
    quick_data = json.loads(quick.stdout)
    deep_data = json.loads(deep.stdout)
    assert quick_data["VALIDATION_MODE"] == "quick"
    assert quick_data["historical_payloads_fully_validated"] is False
    assert deep_data["VALIDATION_MODE"] == "deep"
    assert deep_data["historical_payloads_fully_validated"] is True


def test_50k_populated_quick_status_and_validation_are_bounded_and_counted(
    tmp_path, candidate_factory
):
    database = tmp_path / "populated-50k.sqlite3"
    with CandidateLedger.initialize(database) as ledger:
        _insert_old_structural_rows(ledger, 50_000)
        ledger.append_events(_candidate_events(candidate_factory, 101, start=100_000))

    started = time.perf_counter()
    status = subprocess.run(
        [sys.executable, str(STATUS_CLI), "--database", str(database)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    status_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    validation = subprocess.run(
        [sys.executable, str(VALIDATE_CLI), "--database", str(database)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    validation_ms = (time.perf_counter() - started) * 1000
    assert status.returncode == validation.returncode == 0
    assert "CANDIDATE_OBSERVED_COUNT=50101" in status.stdout
    assert "VALIDATION_MODE=quick" in status.stdout
    assert "HISTORICAL_PAYLOADS_FULLY_VALIDATED=false" in status.stdout
    validation_data = json.loads(validation.stdout)
    assert validation_data["events_checked"] == 100
    assert validation_data["historical_payloads_fully_validated"] is False
    assert status_ms < 5_000
    assert validation_ms < 10_000


def test_quick_validation_checks_recent_payload_hashes(tmp_path, candidate_factory):
    database = tmp_path / "hash.sqlite3"
    candidate = candidate_factory(identity__run_id="hash-run")
    payload_json = canonical_json(candidate)
    with CandidateLedger.initialize(database) as ledger:
        ledger.connection.execute(
            "INSERT INTO candidate_events("
            "event_id,candidate_id,event_type,event_timestamp,schema_version,"
            "payload_json,payload_sha256,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "evt_bad_hash",
                candidate["identity"]["candidate_id"],
                "candidate_observed",
                "2026-07-16T00:00:00Z",
                "1.0.0",
                payload_json,
                sha256_text(payload_json + "changed"),
                "2026-07-16T00:00:00Z",
            ),
        )
    with CandidateLedger.open_read_only(database) as ledger:
        result = ledger.validate_quick()
    assert result["valid"] is False
    assert any("payload hash mismatch" in error for error in result["errors"])
