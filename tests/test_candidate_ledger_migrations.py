from __future__ import annotations

import sqlite3

import pytest

from research.candidate_ledger import migrations
from research.candidate_ledger.migrations import validate_migrations
from research.candidate_ledger.models import ValidationError
from research.candidate_ledger.store import CandidateLedger


def test_initialize_and_reopen_are_migration_idempotent(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        first = validate_migrations(ledger.connection)
    with CandidateLedger.initialize(path) as ledger:
        second = validate_migrations(ledger.connection)
    assert first == second
    assert second["valid"] is True
    assert second["migration_version"] == 2
    assert second["append_only_enforced"] is True
    assert second["required_indexes_present"] is True
    assert "candidate_event_type_time" in second["indexes"]


def test_database_path_must_be_explicit_local_file(tmp_path):
    with pytest.raises(ValidationError, match="explicit local"):
        CandidateLedger.initialize(":memory:")
    with pytest.raises(ValidationError, match="explicit local"):
        CandidateLedger.initialize("file:ledger.sqlite3")
    with pytest.raises(ValidationError, match="parent directory"):
        CandidateLedger.initialize(tmp_path / "missing" / "ledger.sqlite3")


def test_uninitialized_database_is_rejected(tmp_path):
    path = tmp_path / "plain.sqlite3"
    sqlite3.connect(path).close()
    with pytest.raises(ValidationError, match="not an initialized"):
        CandidateLedger(path)


def _create_v1_database(path, *, rows=0):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    migrations._apply_v1(connection)
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-07-16T00:00:00Z')"
    )
    for index in range(rows):
        connection.execute(
            "INSERT INTO candidate_events("
            "event_id,candidate_id,event_type,event_timestamp,schema_version,"
            "payload_json,payload_sha256,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                f"evt_v1_{index}",
                f"cand_v1_{index}",
                "order_attempted",
                "2026-07-16T00:00:00Z",
                "1.0.0",
                "{}",
                "synthetic",
                "2026-07-16T00:00:00Z",
            ),
        )
    connection.commit()
    return connection


def test_v2_index_migration_is_idempotent_and_preserves_populated_history(tmp_path):
    path = tmp_path / "populated-v1.sqlite3"
    connection = _create_v1_database(path, rows=25)
    try:
        assert migrations.migrate(connection) == 2
        assert migrations.migrate(connection) == 2
        assert connection.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0] == 25
        validation = validate_migrations(connection)
        assert validation["valid"] is True
        assert validation["append_only_enforced"] is True
        assert validation["required_indexes_present"] is True
    finally:
        connection.close()


def test_v2_migration_failure_rolls_back_without_history_or_trigger_loss(
    tmp_path, monkeypatch
):
    path = tmp_path / "rollback-v1.sqlite3"
    connection = _create_v1_database(path, rows=3)
    original = migrations._apply_v2

    def fail_after_index_created(target):
        original(target)
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(migrations, "_apply_v2", fail_after_index_created)
    try:
        with pytest.raises(RuntimeError, match="synthetic migration failure"):
            migrations.migrate(connection)
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0] == 3
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('index','trigger')"
            )
        }
        assert "candidate_event_type_time" not in names
        assert {"candidate_events_no_update", "candidate_events_no_delete"}.issubset(names)
    finally:
        connection.close()


def test_future_database_version_fails_closed(tmp_path):
    path = tmp_path / "future.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        ledger.connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (999, '2026-07-16T00:00:00Z')"
        )
    with pytest.raises(ValidationError, match="migration version is unsupported"):
        CandidateLedger.open_read_only(path)
    with pytest.raises(RuntimeError, match="newer than supported"):
        CandidateLedger.initialize(path)
