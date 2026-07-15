from __future__ import annotations

import sqlite3

import pytest

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
    assert second["migration_version"] == 1
    assert second["append_only_enforced"] is True


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
