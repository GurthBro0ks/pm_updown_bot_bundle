#!/usr/bin/env python3
"""Validate and ingest immutable local candidate-capture spool batches."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.candidate_ledger.migrations import LATEST_MIGRATION, validate_migrations
from research.candidate_ledger.spool import SPOOL_VERSION, read_batch, scan_spool
from research.candidate_ledger.store import CandidateLedger, validate_database_path


def _base_fields() -> dict[str, object]:
    return {
        "CANDIDATE_SPOOL_INGEST": "FAIL",
        "OFFLINE_ONLY": "true",
        "DRY_RUN": "false",
        "SPOOL_VERSION": SPOOL_VERSION,
        "BATCHES_SCANNED": 0,
        "BATCHES_VALID": 0,
        "BATCHES_INGESTED": 0,
        "BATCHES_ALREADY_PRESENT": 0,
        "BATCHES_FAILED": 0,
        "EVENTS_INGESTED": 0,
        "SPOOL_BYTES": 0,
        "DATABASE_SCHEMA_VERSION": 0,
        "APPEND_ONLY_ENFORCED": "false",
        "RAW_CANDIDATES_INCLUDED": "false",
        "VALUES_PRINTED": "no_secret_values",
    }


def ingest(
    spool: Path,
    database: Path,
    *,
    dry_run: bool = False,
    timeout_ms: int = 250,
) -> tuple[dict[str, object], int]:
    fields = _base_fields()
    fields["DRY_RUN"] = str(dry_run).lower()
    try:
        scan = scan_spool(spool)
        fields["BATCHES_SCANNED"] = scan.batch_count
        fields["SPOOL_BYTES"] = scan.total_bytes
        valid: list[dict[str, Any]] = []
        for path in scan.batch_paths:
            try:
                valid.append(read_batch(path))
            except Exception:
                fields["BATCHES_FAILED"] = int(fields["BATCHES_FAILED"]) + 1
        fields["BATCHES_VALID"] = len(valid)
        resolved_database = validate_database_path(database)
        if dry_run:
            if resolved_database.is_file():
                with CandidateLedger.open_read_only(resolved_database) as ledger:
                    migration = validate_migrations(ledger.connection)
                fields["DATABASE_SCHEMA_VERSION"] = migration["migration_version"]
                fields["APPEND_ONLY_ENFORCED"] = str(
                    bool(migration["append_only_enforced"])
                ).lower()
            else:
                fields["DATABASE_SCHEMA_VERSION"] = LATEST_MIGRATION
                fields["APPEND_ONLY_ENFORCED"] = "true"
            fields["CANDIDATE_SPOOL_INGEST"] = (
                "PASS" if int(fields["BATCHES_FAILED"]) == 0 else "WARN"
            )
            return fields, 0 if fields["CANDIDATE_SPOOL_INGEST"] == "PASS" else 1

        if not resolved_database.exists():
            descriptor = os.open(
                resolved_database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.close(descriptor)
            ledger_factory = CandidateLedger.initialize
        else:
            ledger_factory = CandidateLedger
        with ledger_factory(resolved_database, timeout_ms=timeout_ms) as ledger:
            migration = validate_migrations(ledger.connection)
            fields["DATABASE_SCHEMA_VERSION"] = migration["migration_version"]
            fields["APPEND_ONLY_ENFORCED"] = str(
                bool(migration["append_only_enforced"])
            ).lower()
            for envelope in valid:
                try:
                    outcome, results = ledger.ingest_spool_batch(
                        batch_id=envelope["batch_id"],
                        body_sha256=envelope["body_sha256"],
                        events=envelope["body"]["events"],
                    )
                    if outcome == "already_present":
                        fields["BATCHES_ALREADY_PRESENT"] = int(
                            fields["BATCHES_ALREADY_PRESENT"]
                        ) + 1
                    else:
                        fields["BATCHES_INGESTED"] = int(fields["BATCHES_INGESTED"]) + 1
                        fields["EVENTS_INGESTED"] = int(fields["EVENTS_INGESTED"]) + sum(
                            1 for result in results if result.appended
                        )
                except Exception:
                    fields["BATCHES_FAILED"] = int(fields["BATCHES_FAILED"]) + 1
        fields["CANDIDATE_SPOOL_INGEST"] = (
            "PASS" if int(fields["BATCHES_FAILED"]) == 0 else "WARN"
        )
        return fields, 0 if fields["CANDIDATE_SPOOL_INGEST"] == "PASS" else 1
    except sqlite3.OperationalError:
        fields["BATCHES_FAILED"] = max(
            int(fields["BATCHES_FAILED"]), int(fields["BATCHES_VALID"])
        )
        fields["CANDIDATE_SPOOL_INGEST"] = "WARN"
        return fields, 1
    except Exception:
        fields["BATCHES_FAILED"] = max(
            int(fields["BATCHES_FAILED"]), int(fields["BATCHES_VALID"])
        )
        fields["CANDIDATE_SPOOL_INGEST"] = "FAIL"
        return fields, 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline local spool-to-SQLite ingest.")
    parser.add_argument("--spool", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=250)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_ms <= 10_000:
        parser.error("--timeout-ms must be from 1 to 10000")
    fields, exit_code = ingest(
        args.spool.expanduser(),
        args.database.expanduser(),
        dry_run=args.dry_run,
        timeout_ms=args.timeout_ms,
    )
    for name, value in fields.items():
        print(f"{name}={value}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
