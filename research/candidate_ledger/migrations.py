"""SQLite schema migrations for the append-only candidate ledger."""

from __future__ import annotations

import sqlite3

from .models import EVENT_TYPES, utc_now


LATEST_MIGRATION = 3

EXPECTED_EVENT_COLUMNS = (
    "sequence",
    "event_id",
    "candidate_id",
    "event_type",
    "event_timestamp",
    "schema_version",
    "payload_json",
    "payload_sha256",
    "created_at",
)
EXPECTED_APPEND_ONLY_TRIGGERS = {
    "candidate_events_no_update",
    "candidate_events_no_delete",
    "candidate_spool_batches_no_update",
    "candidate_spool_batches_no_delete",
}
EXPECTED_INDEXES = {
    "one_candidate_snapshot",
    "candidate_event_history",
    "candidate_event_type_time",
}


def _apply_v1(connection: sqlite3.Connection) -> None:
    event_types = ",".join(f"'{event_type}'" for event_type in EVENT_TYPES)
    connection.execute(
        f"""
        CREATE TABLE candidate_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            candidate_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ({event_types})),
            event_timestamp TEXT NOT NULL,
            schema_version TEXT NOT NULL CHECK (schema_version = '1.0.0'),
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX one_candidate_snapshot ON candidate_events(candidate_id) WHERE event_type = 'candidate_observed'"
    )
    connection.execute(
        "CREATE INDEX candidate_event_history ON candidate_events(candidate_id, sequence)"
    )
    connection.execute(
        """
        CREATE TRIGGER candidate_events_no_update
        BEFORE UPDATE ON candidate_events
        BEGIN
            SELECT RAISE(ABORT, 'candidate_events is append-only');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER candidate_events_no_delete
        BEFORE DELETE ON candidate_events
        BEGIN
            SELECT RAISE(ABORT, 'candidate_events is append-only');
        END
        """
    )


def _apply_v2(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX candidate_event_type_time "
        "ON candidate_events(event_type, event_timestamp DESC, sequence DESC)"
    )


def _apply_v3(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE candidate_spool_batches (
            batch_id TEXT PRIMARY KEY,
            body_sha256 TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            ingested_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TRIGGER candidate_spool_batches_no_update
        BEFORE UPDATE ON candidate_spool_batches
        BEGIN
            SELECT RAISE(ABORT, 'candidate_spool_batches is append-only');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER candidate_spool_batches_no_delete
        BEFORE DELETE ON candidate_spool_batches
        BEGIN
            SELECT RAISE(ABORT, 'candidate_spool_batches is append-only');
        END
        """
    )


def migrate(connection: sqlite3.Connection) -> int:
    """Apply all migrations transactionally and return the current version."""

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        current = int(row[0])
        if current > LATEST_MIGRATION:
            raise RuntimeError(f"database migration version {current} is newer than supported {LATEST_MIGRATION}")
        if current < 1:
            _apply_v1(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, utc_now()),
            )
            current = 1
        if current < 2:
            _apply_v2(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (2, utc_now()),
            )
            current = 2
        if current < 3:
            _apply_v3(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (3, utc_now()),
            )
            current = 3
        connection.commit()
        return current
    except Exception:
        connection.rollback()
        raise


def validate_migrations(connection: sqlite3.Connection) -> dict[str, object]:
    """Validate versioned schema metadata, append-only triggers, and integrity."""

    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    version = int(row[0])
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('candidate_events','candidate_spool_batches')"
        )
    }
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='candidate_events'"
        )
    }
    columns = tuple(
        str(row[1]) for row in connection.execute("PRAGMA table_info(candidate_events)")
    )
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    append_only_enforced = EXPECTED_APPEND_ONLY_TRIGGERS.issubset(triggers)
    required_indexes_present = EXPECTED_INDEXES.issubset(indexes)
    schema_metadata_valid = columns == EXPECTED_EVENT_COLUMNS
    return {
        "migration_version": version,
        "migration_current": version == LATEST_MIGRATION,
        "append_only_triggers": sorted(triggers),
        "append_only_enforced": append_only_enforced,
        "indexes": sorted(indexes),
        "required_indexes_present": required_indexes_present,
        "schema_columns": list(columns),
        "schema_metadata_valid": schema_metadata_valid,
        "integrity": integrity,
        "valid": (
            version == LATEST_MIGRATION
            and append_only_enforced
            and required_indexes_present
            and schema_metadata_valid
            and integrity == "ok"
        ),
    }
