#!/usr/bin/env python3
"""Print bounded capture, spool, ingest, and ledger status without payload values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.candidate_ledger import CandidateLedger, CaptureConfig
from research.candidate_ledger.spool import SPOOL_VERSION, scan_spool


EVENT_FIELD_NAMES = {
    "candidate_observed": "CANDIDATE_OBSERVED_COUNT",
    "gate_evaluated": "GATE_EVALUATED_COUNT",
    "order_intent_created": "ORDER_INTENT_COUNT",
    "order_attempted": "ORDER_ATTEMPT_COUNT",
    "order_result": "ORDER_RESULT_COUNT",
}


def _base_fields() -> dict[str, object]:
    config = CaptureConfig.from_environment()
    return {
        "CANDIDATE_CAPTURE_STATUS": "WARN",
        "OFFLINE_OR_SHADOW_ONLY": "true",
        "SHADOW_CAPTURE_ENABLED": str(config.enabled).lower(),
        "RUNTIME_CAPTURE_MODE": config.runtime_mode,
        "RUNTIME_BATCH_WRITTEN": "not_run",
        "RUNTIME_BATCH_DROPPED": 0,
        "RUNTIME_WARNING_COUNT": len(config.warning_codes),
        "SPOOL_EXISTS": "no",
        "SPOOL_VERSION": SPOOL_VERSION,
        "SPOOL_BATCH_COUNT": 0,
        "SPOOL_PENDING_BATCH_COUNT": 0,
        "SPOOL_BYTES": 0,
        "LEDGER_EXISTS": "no",
        "LEDGER_EVENT_COUNT": 0,
        "LAST_SUCCESSFUL_INGEST": "none",
        "DATABASE_EXISTS": "no",
        "SCHEMA_VERSION": "",
        "MIGRATION_VERSION": 0,
        "MIGRATION_VALID": "false",
        "APPEND_ONLY_ENFORCED": "false",
        "SCHEMA_METADATA_VALID": "false",
        "REQUIRED_INDEXES_PRESENT": "false",
        "INTEGRITY_STATUS": "unknown",
        "VALIDATION_MODE": "quick",
        "HISTORICAL_PAYLOADS_FULLY_VALIDATED": "false",
        "RECENT_EVENTS_CHECKED": 0,
        "LATEST_RUN_ID": "none",
        "LATEST_RUN_TIMESTAMP": "none",
        "CANDIDATE_OBSERVED_COUNT": 0,
        "GATE_EVALUATED_COUNT": 0,
        "ORDER_INTENT_COUNT": 0,
        "ORDER_ATTEMPT_COUNT": 0,
        "ORDER_RESULT_COUNT": 0,
        "CAPTURE_WARNING_COUNT": len(config.warning_codes),
        "RAW_CANDIDATES_INCLUDED": "false",
        "VALUES_PRINTED": "no_secret_values",
    }


def _fields(spool: Path | None, database: Path | None) -> tuple[dict[str, object], int]:
    output = _base_fields()
    warning_count = int(output["CAPTURE_WARNING_COUNT"])
    spool_batch_ids: set[str] = set()
    if spool is not None:
        if spool.is_dir():
            output["SPOOL_EXISTS"] = "yes"
            try:
                scan = scan_spool(spool)
                output["SPOOL_BATCH_COUNT"] = scan.batch_count
                output["SPOOL_PENDING_BATCH_COUNT"] = scan.batch_count
                output["SPOOL_BYTES"] = scan.total_bytes
                spool_batch_ids = {
                    path.name.removesuffix(".clspool") for path in scan.batch_paths
                }
            except Exception:
                warning_count += 1
        else:
            warning_count += 1

    if database is not None and database.is_file():
        output["DATABASE_EXISTS"] = "yes"
        output["LEDGER_EXISTS"] = "yes"
        try:
            with CandidateLedger.open_read_only(database) as ledger:
                summary = ledger.summary()
                validation = ledger.validate_quick()
                output["SCHEMA_VERSION"] = summary["schema_version"]
                output["LEDGER_EVENT_COUNT"] = int(summary["event_count"])
                output["MIGRATION_VERSION"] = int(validation["migration"]["migration_version"])
                output["MIGRATION_VALID"] = str(
                    bool(validation["migration"]["migration_current"])
                ).lower()
                output["APPEND_ONLY_ENFORCED"] = str(
                    bool(validation["migration"]["append_only_enforced"])
                ).lower()
                output["SCHEMA_METADATA_VALID"] = str(
                    bool(validation["migration"]["schema_metadata_valid"])
                ).lower()
                output["REQUIRED_INDEXES_PRESENT"] = str(
                    bool(validation["migration"]["required_indexes_present"])
                ).lower()
                output["INTEGRITY_STATUS"] = str(validation["migration"]["integrity"])
                output["VALIDATION_MODE"] = str(validation["validation_mode"])
                output["RECENT_EVENTS_CHECKED"] = int(validation["events_checked"])
                for event_type, field_name in EVENT_FIELD_NAMES.items():
                    output[field_name] = int(summary["event_counts"].get(event_type, 0))
                row = ledger.connection.execute(
                    "SELECT payload_json FROM candidate_events "
                    "WHERE event_type='candidate_observed' "
                    "ORDER BY event_timestamp DESC,sequence DESC LIMIT 1"
                ).fetchone()
                if row:
                    payload = json.loads(row[0])
                    output["LATEST_RUN_ID"] = str(payload["identity"]["run_id"])[:128]
                    output["LATEST_RUN_TIMESTAMP"] = str(
                        payload["identity"]["run_timestamp"]
                    )[:64]
                ingest_row = ledger.connection.execute(
                    "SELECT MAX(ingested_at) FROM candidate_spool_batches"
                ).fetchone()
                if ingest_row and ingest_row[0]:
                    output["LAST_SUCCESSFUL_INGEST"] = str(ingest_row[0])[:64]
                if spool_batch_ids:
                    ingested_ids = {
                        str(item[0])
                        for item in ledger.connection.execute(
                            "SELECT batch_id FROM candidate_spool_batches"
                        )
                    }
                    output["SPOOL_PENDING_BATCH_COUNT"] = len(
                        spool_batch_ids - ingested_ids
                    )
                if not validation["valid"]:
                    warning_count += min(20, int(validation["error_count"])) or 1
        except Exception:
            warning_count += 1
    elif database is not None:
        warning_count += 1

    output["CAPTURE_WARNING_COUNT"] = warning_count
    output["CANDIDATE_CAPTURE_STATUS"] = "PASS" if warning_count == 0 else "WARN"
    return output, 0 if warning_count == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print redacted candidate-capture status.")
    parser.add_argument("--spool", type=Path, help="Explicit local spool directory.")
    parser.add_argument("--database", type=Path, help="Explicit local SQLite database path.")
    args = parser.parse_args(argv)
    if args.spool is None and args.database is None:
        parser.error("at least one of --spool or --database is required")
    fields, exit_code = _fields(
        args.spool.expanduser().resolve() if args.spool else None,
        args.database.expanduser().resolve() if args.database else None,
    )
    for name, value in fields.items():
        print(f"{name}={value}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
