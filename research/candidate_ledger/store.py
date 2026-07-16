"""Transactional append-only SQLite event store for offline research."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import quote

from .migrations import LATEST_MIGRATION, migrate, validate_migrations
from .models import (
    SCHEMA_VERSION,
    AppendResult,
    DuplicateCandidateError,
    DuplicateEventError,
    LeakageError,
    StoredEvent,
    ValidationError,
    canonical_json,
    deterministic_event_id,
    parse_utc_timestamp,
    sha256_text,
    utc_now,
)
from .schema import validate_candidate_payload, validate_event_payload
from .splits import ExperimentCutoffs, validate_assignment, validate_immutable_assignment


MAX_EVENT_BYTES = 512 * 1024


def validate_database_path(database_path: str | Path) -> Path:
    """Require a concrete local filesystem path with an existing parent."""

    raw = str(database_path)
    if not raw or raw == ":memory:" or raw.startswith("file:") or "://" in raw:
        raise ValidationError("an explicit local filesystem database path is required")
    path = Path(raw).expanduser().resolve()
    if not path.parent.is_dir():
        raise ValidationError("database parent directory does not exist")
    if path.exists() and not path.is_file():
        raise ValidationError("database path must name a file")
    return path


class CandidateLedger:
    """One-writer append API plus bounded read-only queries."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        initialize: bool = False,
        read_only: bool = False,
        timeout_ms: int = 5000,
    ):
        self.path = validate_database_path(database_path)
        self.read_only = read_only
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 10_000:
            raise ValidationError("timeout_ms must be an integer from 1 to 10000")
        timeout_seconds = timeout_ms / 1000.0
        if read_only:
            if not self.path.is_file():
                raise ValidationError("database does not exist")
            uri = f"file:{quote(str(self.path))}?mode=ro"
            self.connection = sqlite3.connect(
                uri, uri=True, isolation_level=None, timeout=timeout_seconds
            )
        else:
            if not initialize and not self.path.is_file():
                raise ValidationError("database does not exist; initialize it explicitly")
            self.connection = sqlite3.connect(
                self.path, isolation_level=None, timeout=timeout_seconds
            )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        if initialize:
            if read_only:
                raise ValidationError("read-only ledger cannot initialize a database")
            migrate(self.connection)
        else:
            self._require_initialized()

    @classmethod
    def initialize(
        cls, database_path: str | Path, *, timeout_ms: int = 5000
    ) -> "CandidateLedger":
        """Create or migrate an explicitly named local database."""

        return cls(database_path, initialize=True, timeout_ms=timeout_ms)

    @classmethod
    def open_read_only(cls, database_path: str | Path) -> "CandidateLedger":
        """Open an initialized database with SQLite `mode=ro`."""

        return cls(database_path, read_only=True)

    def _require_initialized(self) -> None:
        try:
            row = self.connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValidationError("database is not an initialized candidate ledger") from exc
        if not row or int(row[0]) != LATEST_MIGRATION:
            raise ValidationError("database migration version is unsupported")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CandidateLedger":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _candidate_snapshot(
        self,
        candidate_id: str,
        cache: dict[str, Mapping[str, Any] | None] | None = None,
    ) -> Mapping[str, Any] | None:
        if cache is not None and candidate_id in cache:
            return cache[candidate_id]
        row = self.connection.execute(
            "SELECT payload_json FROM candidate_events WHERE candidate_id=? AND event_type='candidate_observed'",
            (candidate_id,),
        ).fetchone()
        snapshot = json.loads(row[0]) if row else None
        if cache is not None:
            cache[candidate_id] = snapshot
        return snapshot

    def _existing_assignment(self, candidate_id: str, experiment_id: str) -> Mapping[str, Any] | None:
        rows = self.connection.execute(
            "SELECT payload_json FROM candidate_events WHERE candidate_id=? AND event_type='experiment_assignment' ORDER BY sequence",
            (candidate_id,),
        )
        for row in rows:
            payload = json.loads(row[0])
            if payload["experiment_id"] == experiment_id:
                return payload
        return None

    def _validate_event_against_history(
        self,
        *,
        candidate_id: str,
        event_type: str,
        event_timestamp: str,
        payload: Mapping[str, Any],
        candidate_snapshots: dict[str, Mapping[str, Any] | None] | None = None,
    ) -> None:
        if event_type == "candidate_observed":
            if payload["identity"]["candidate_id"] != candidate_id:
                raise ValidationError("candidate_id does not match candidate_observed payload")
            return
        snapshot = self._candidate_snapshot(candidate_id, candidate_snapshots)
        if snapshot is None:
            raise ValidationError("candidate_observed must be appended before later events")
        run_time = parse_utc_timestamp(snapshot["identity"]["run_timestamp"], field="candidate.run_timestamp")
        if parse_utc_timestamp(event_timestamp, field="event_timestamp") < run_time:
            raise LeakageError("event_timestamp cannot precede candidate run_timestamp")
        if event_type == "settlement_observed":
            resolved = parse_utc_timestamp(payload["resolved_at"], field="settlement.resolved_at")
            if resolved < run_time:
                raise LeakageError("settlement cannot precede candidate run_timestamp")
        if event_type == "experiment_assignment":
            cutoffs = ExperimentCutoffs(
                train_end=payload["train_end"],
                validation_end=payload["validation_end"],
                holdout_end=payload["holdout_end"],
            )
            validate_assignment(
                snapshot,
                split=payload["split"],
                strategy_version=payload["strategy_version"],
                cutoffs=cutoffs,
            )
            existing = self._existing_assignment(candidate_id, payload["experiment_id"])
            validate_immutable_assignment(existing, payload)
            if existing is not None:
                raise LeakageError("experiment assignment is append-once")
        if event_type == "review_recorded" and payload["accepted_into_knowledge_base"]:
            settled = self.connection.execute(
                "SELECT 1 FROM candidate_events WHERE candidate_id=? AND event_type='settlement_observed' LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if settled is None or payload["provenance"] != "outer_loop_settlement":
                raise ValidationError("knowledge-base acceptance requires prior settlement and outer-loop provenance")

    def append_event(
        self,
        *,
        candidate_id: str,
        event_type: str,
        event_timestamp: str,
        payload: Mapping[str, Any],
        event_id: str | None = None,
    ) -> AppendResult:
        """Append once transactionally; identical retries are idempotent."""

        prepared = self._prepare_event(
            candidate_id=candidate_id,
            event_type=event_type,
            event_timestamp=event_timestamp,
            payload=payload,
            event_id=event_id,
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            result = self._append_prepared_event(prepared, {})
            self.connection.commit()
            return result
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def append_events(self, events: Iterable[Mapping[str, Any]]) -> list[AppendResult]:
        """Append a bounded caller-supplied batch in one transaction."""

        prepared = [
            self._prepare_event(
                candidate_id=event["candidate_id"],
                event_type=event["event_type"],
                event_timestamp=event["event_timestamp"],
                payload=event["payload"],
                event_id=event.get("event_id"),
            )
            for event in events
        ]
        if not prepared:
            return []
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            candidate_snapshots: dict[str, Mapping[str, Any] | None] = {}
            results = [
                self._append_prepared_event(event, candidate_snapshots)
                for event in prepared
            ]
            self.connection.commit()
            return results
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def _prepare_event(
        self,
        *,
        candidate_id: str,
        event_type: str,
        event_timestamp: str,
        payload: Mapping[str, Any],
        event_id: str | None,
    ) -> dict[str, Any]:
        if self.read_only:
            raise ValidationError("read-only ledger cannot append")
        parse_utc_timestamp(event_timestamp, field="event_timestamp")
        validate_event_payload(event_type, payload)
        if event_type == "candidate_observed":
            validate_candidate_payload(payload)
        payload_json = canonical_json(payload)
        if len(payload_json.encode("utf-8")) > MAX_EVENT_BYTES:
            raise ValidationError("event payload exceeds bounded size")
        payload_sha256 = sha256_text(payload_json)
        resolved_event_id = event_id or deterministic_event_id(
            candidate_id=candidate_id,
            event_type=event_type,
            event_timestamp=event_timestamp,
            payload=payload,
        )
        if not resolved_event_id.startswith("evt_") or len(resolved_event_id) > 128:
            raise ValidationError("event_id must be a bounded evt_ identifier")
        return {
            "candidate_id": candidate_id,
            "event_type": event_type,
            "event_timestamp": event_timestamp,
            "payload": payload,
            "payload_json": payload_json,
            "payload_sha256": payload_sha256,
            "event_id": resolved_event_id,
        }

    def _append_prepared_event(
        self,
        event: Mapping[str, Any],
        candidate_snapshots: dict[str, Mapping[str, Any] | None],
    ) -> AppendResult:
        candidate_id = event["candidate_id"]
        event_type = event["event_type"]
        event_timestamp = event["event_timestamp"]
        payload = event["payload"]
        payload_json = event["payload_json"]
        payload_sha256 = event["payload_sha256"]
        resolved_event_id = event["event_id"]
        existing = self.connection.execute(
                "SELECT sequence,candidate_id,event_type,event_timestamp,schema_version,payload_sha256 FROM candidate_events WHERE event_id=?",
                (resolved_event_id,),
            ).fetchone()
        if existing:
            same = (
                existing["candidate_id"] == candidate_id
                and existing["event_type"] == event_type
                and existing["event_timestamp"] == event_timestamp
                and existing["schema_version"] == SCHEMA_VERSION
                and existing["payload_sha256"] == payload_sha256
            )
            if same:
                return AppendResult(resolved_event_id, int(existing["sequence"]), False)
            raise DuplicateEventError("event_id already exists with different content")

        self._validate_event_against_history(
            candidate_id=candidate_id,
            event_type=event_type,
            event_timestamp=event_timestamp,
            payload=payload,
            candidate_snapshots=candidate_snapshots,
        )
        if event_type == "candidate_observed" and self._candidate_snapshot(
            candidate_id, candidate_snapshots
        ) is not None:
            raise DuplicateCandidateError("candidate snapshot already exists")
        cursor = self.connection.execute(
                """
                INSERT INTO candidate_events(
                    event_id,candidate_id,event_type,event_timestamp,schema_version,
                    payload_json,payload_sha256,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    resolved_event_id,
                    candidate_id,
                    event_type,
                    event_timestamp,
                    SCHEMA_VERSION,
                    payload_json,
                    payload_sha256,
                    utc_now(),
                ),
            )
        if event_type == "candidate_observed":
            candidate_snapshots[candidate_id] = payload
        return AppendResult(resolved_event_id, int(cursor.lastrowid), True)

    def history(self, candidate_id: str) -> list[StoredEvent]:
        """Return one candidate's ordered event history."""

        rows = self.connection.execute(
            "SELECT * FROM candidate_events WHERE candidate_id=? ORDER BY sequence",
            (candidate_id,),
        )
        return [
            StoredEvent(
                sequence=int(row["sequence"]),
                event_id=row["event_id"],
                candidate_id=row["candidate_id"],
                event_type=row["event_type"],
                event_timestamp=row["event_timestamp"],
                schema_version=row["schema_version"],
                payload=json.loads(row["payload_json"]),
                payload_sha256=row["payload_sha256"],
            )
            for row in rows
        ]

    def candidate_payloads(self) -> Iterator[Mapping[str, Any]]:
        """Yield candidate snapshots in deterministic observation order."""

        rows = self.connection.execute(
            "SELECT payload_json FROM candidate_events WHERE event_type='candidate_observed' ORDER BY event_timestamp,candidate_id"
        )
        for row in rows:
            yield json.loads(row[0])

    def summary(self) -> dict[str, Any]:
        """Return bounded counts only; never raw candidate data."""

        event_counts = {
            row["event_type"]: int(row["count"])
            for row in self.connection.execute(
                "SELECT event_type,COUNT(*) AS count FROM candidate_events GROUP BY event_type ORDER BY event_type"
            )
        }
        candidate_count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM candidate_events WHERE event_type='candidate_observed'"
            ).fetchone()[0]
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "candidate_count": candidate_count,
            "event_count": sum(event_counts.values()),
            "event_counts": event_counts,
            "raw_candidates_included": False,
        }

    def _validate_rows(
        self,
        rows: Iterable[sqlite3.Row],
        *,
        exhaustive_relationships: bool = False,
    ) -> tuple[int, list[str]]:
        checked = 0
        errors: list[str] = []
        observed_candidates: set[str] = set()
        for row in rows:
            checked += 1
            try:
                payload = json.loads(row["payload_json"])
                if sha256_text(canonical_json(payload)) != row["payload_sha256"]:
                    raise ValidationError("payload hash mismatch")
                if row["schema_version"] != SCHEMA_VERSION:
                    raise ValidationError("unsupported stored schema version")
                validate_event_payload(row["event_type"], payload)
                if (
                    row["event_type"] == "candidate_observed"
                    and payload["identity"]["candidate_id"] != row["candidate_id"]
                ):
                    raise ValidationError("stored candidate id mismatch")
            except (json.JSONDecodeError, ValidationError) as exc:
                errors.append(f"sequence {row['sequence']}: {exc}")
            if exhaustive_relationships:
                candidate_id = str(row["candidate_id"])
                if row["event_type"] == "candidate_observed":
                    observed_candidates.add(candidate_id)
                elif candidate_id not in observed_candidates:
                    errors.append(
                        f"sequence {row['sequence']}: candidate_observed relationship missing or out of order"
                    )
        return checked, errors

    def validate_quick(self, *, recent_event_limit: int = 100) -> dict[str, Any]:
        """Validate structure and a bounded recent payload window."""

        if type(recent_event_limit) is not int or not 1 <= recent_event_limit <= 1000:
            raise ValidationError("recent_event_limit must be an integer from 1 to 1000")
        migration = validate_migrations(self.connection)
        rows = self.connection.execute(
            "SELECT * FROM candidate_events ORDER BY sequence DESC LIMIT ?",
            (recent_event_limit,),
        ).fetchall()
        checked, errors = self._validate_rows(rows)
        checked_relationships: set[str] = set()
        for row in rows:
            candidate_id = str(row["candidate_id"])
            if row["event_type"] == "candidate_observed" or candidate_id in checked_relationships:
                continue
            checked_relationships.add(candidate_id)
            snapshot = self.connection.execute(
                "SELECT 1 FROM candidate_events "
                "WHERE candidate_id=? AND event_type='candidate_observed' LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if snapshot is None:
                errors.append(
                    f"sequence {row['sequence']}: candidate_observed relationship missing"
                )
        return {
            "valid": bool(migration["valid"]) and not errors,
            "validation_mode": "quick",
            "migration": migration,
            "events_checked": checked,
            "recent_event_limit": recent_event_limit,
            "historical_payloads_fully_validated": False,
            "scope": "structural integrity plus bounded recent payload and relationship checks",
            "error_count": len(errors),
            "errors": errors[:20],
        }

    def validate_deep(self) -> dict[str, Any]:
        """Exhaustively validate all payloads and candidate/event relationships."""

        migration = validate_migrations(self.connection)
        rows = self.connection.execute("SELECT * FROM candidate_events ORDER BY sequence")
        checked, errors = self._validate_rows(rows, exhaustive_relationships=True)
        return {
            "valid": bool(migration["valid"]) and not errors,
            "validation_mode": "deep",
            "migration": migration,
            "events_checked": checked,
            "recent_event_limit": None,
            "historical_payloads_fully_validated": True,
            "scope": "exhaustive historical payload, hash, schema, and relationship checks",
            "error_count": len(errors),
            "errors": errors[:20],
        }

    def validate(self) -> dict[str, Any]:
        """Backward-compatible exhaustive validation alias."""

        return self.validate_deep()
