from __future__ import annotations

import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

from research.candidate_ledger import CandidateLedger
from research.candidate_ledger.benchmark import run_benchmark, validate_benchmark_root


def test_accumulated_benchmark_covers_capture_safety_contract(tmp_path, monkeypatch):
    real_socket = socket.socket

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", forbidden_network)
    result = run_benchmark(tmp_path, scales=(0, 12, 40), repeats=2, append_candidates=4)
    monkeypatch.setattr(socket, "socket", real_socket)

    assert result["classification"] == "PASS_BOUNDED"
    assert result["network_used"] is False
    assert result["production_path_used"] is False
    assert result["raw_candidate_values_included"] is False
    assert result["scales"] == [0, 12, 40]
    accumulated = result["results"][-1]
    assert accumulated["history_population"]["candidate_count"] == 40
    assert accumulated["history_population"]["event_count"] == 140
    assert accumulated["append_end_to_end_latency"]["candidate_count_per_run"] == 4
    assert accumulated["batch_limit"]["enforced"] is True
    assert accumulated["idempotency"]["preserved"] is True
    assert accumulated["append_only"] == {"update_rejected": True, "delete_rejected": True}
    assert accumulated["status_cli_latency"]["p99_ms"] >= 0
    assert accumulated["quick_validation_latency"]["events_checked"] == 100
    assert accumulated["quick_validation_latency"]["validation_mode"] == "quick"
    assert accumulated["deep_validation_latency"]["events_checked"] > 140
    assert accumulated["deep_validation_latency"]["validation_mode"] == "deep"
    assert accumulated["migration_latency"]["history_preserved"] is True
    assert accumulated["migration_latency"]["required_indexes_present"] is True
    assert result["capture_write_path_classification"] == "PASS_OFF_CRITICAL_PATH"
    assert result["runtime_capture_path_classification"] == "PASS_OFF_CRITICAL_PATH"
    assert result["event_completeness_classification"] == "PASS_COMPLETE"
    assert accumulated["append_flush_latency"]["runtime_sqlite_access"] is False
    assert result["read_path_classification"] == "PASS_BOUNDED"
    assert result["lock_timeout"]["failure_isolated"] is True
    assert result["lock_timeout"]["post_lock_validation_valid"] is True


def test_benchmark_output_is_redacted_and_has_no_database_paths(tmp_path):
    result = run_benchmark(tmp_path, scales=(0, 5), repeats=2, append_candidates=2)
    serialized = json.dumps(result, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "Synthetic accumulated-ledger candidate" not in serialized
    assert "SYNTHETIC-BENCH-" not in serialized
    assert "raw_prompt" not in serialized
    assert "private_key" not in serialized
    assert result["synthetic_local_only"] is True
    assert result["production_latency_claimed"] is False


def test_benchmark_refuses_non_temporary_or_production_paths():
    with pytest.raises(ValueError, match="dedicated temporary directory"):
        validate_benchmark_root(Path("/opt/slimy/pm_updown_bot_bundle"))
    with pytest.raises(ValueError, match="dedicated temporary directory"):
        validate_benchmark_root(Path("/tmp"))


def test_accumulated_database_still_rejects_direct_mutation(tmp_path):
    result = run_benchmark(tmp_path, scales=(0, 25), repeats=2, append_candidates=3)
    assert result["results"][-1]["append_only"]["update_rejected"] is True
    assert result["results"][-1]["append_only"]["delete_rejected"] is True


def test_benchmark_cli_is_direct_and_redacted(tmp_path):
    output = tmp_path / "result.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/candidate_ledger_accumulated_benchmark.py",
            "--repeats",
            "2",
            "--scales",
            "0,10",
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode in {0, 2}
    payload = json.loads(output.read_text())
    assert payload["scales"] == [0, 10]
    assert payload["network_used"] is False
    assert "Synthetic accumulated-ledger candidate" not in output.read_text()


def test_lock_timeout_does_not_corrupt_accumulated_database(tmp_path):
    database = tmp_path / "manual.sqlite3"
    with CandidateLedger.initialize(database):
        pass
    lock = sqlite3.connect(database, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    lock.rollback()
    lock.close()
    with CandidateLedger.open_read_only(database) as ledger:
        assert ledger.validate()["valid"] is True
