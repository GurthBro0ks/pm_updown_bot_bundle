"""SQLite schema migrations for the append-only candidate ledger."""

from __future__ import annotations

import sqlite3

from .models import EVENT_TYPES, utc_now


LATEST_MIGRATION = 1


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
        connection.commit()
        return current
    except Exception:
        connection.rollback()
        raise


def validate_migrations(connection: sqlite3.Connection) -> dict[str, object]:
    """Validate migration version, append-only triggers, and SQLite integrity."""

    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    version = int(row[0])
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='candidate_events'"
        )
    }
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    expected = {"candidate_events_no_update", "candidate_events_no_delete"}
    return {
        "migration_version": version,
        "migration_current": version == LATEST_MIGRATION,
        "append_only_triggers": sorted(triggers),
        "append_only_enforced": expected.issubset(triggers),
        "integrity": integrity,
        "valid": version == LATEST_MIGRATION and expected.issubset(triggers) and integrity == "ok",
    }
