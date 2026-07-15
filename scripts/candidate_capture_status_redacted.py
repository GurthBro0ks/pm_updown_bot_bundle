#!/usr/bin/env python3
"""Print bounded candidate-capture health without candidate payload values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.candidate_ledger import CandidateLedger


EVENT_FIELD_NAMES = {
    "candidate_observed": "CANDIDATE_OBSERVED_COUNT",
    "gate_evaluated": "GATE_EVALUATED_COUNT",
    "order_intent_created": "ORDER_INTENT_COUNT",
    "order_attempted": "ORDER_ATTEMPT_COUNT",
    "order_result": "ORDER_RESULT_COUNT",
}


def _fields(database: Path) -> tuple[dict[str, object], int]:
    output: dict[str, object] = {
        "CANDIDATE_CAPTURE_STATUS": "WARN",
        "OFFLINE_OR_SHADOW_ONLY": "true",
        "DATABASE_EXISTS": "yes" if database.is_file() else "no",
        "SCHEMA_VERSION": "",
        "MIGRATION_VALID": "false",
        "APPEND_ONLY_ENFORCED": "false",
        "LATEST_RUN_ID": "none",
        "LATEST_RUN_TIMESTAMP": "none",
        "CANDIDATE_OBSERVED_COUNT": 0,
        "GATE_EVALUATED_COUNT": 0,
        "ORDER_INTENT_COUNT": 0,
        "ORDER_ATTEMPT_COUNT": 0,
        "ORDER_RESULT_COUNT": 0,
        "CAPTURE_WARNING_COUNT": 0,
        "RAW_CANDIDATES_INCLUDED": "false",
        "VALUES_PRINTED": "no_secret_values",
    }
    if not database.is_file():
        output["CAPTURE_WARNING_COUNT"] = 1
        return output, 1
    try:
        with CandidateLedger.open_read_only(database) as ledger:
            summary = ledger.summary()
            validation = ledger.validate()
            output["SCHEMA_VERSION"] = summary["schema_version"]
            output["MIGRATION_VALID"] = str(bool(validation["migration"]["migration_current"])).lower()
            output["APPEND_ONLY_ENFORCED"] = str(bool(validation["migration"]["append_only_enforced"])).lower()
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
                output["LATEST_RUN_TIMESTAMP"] = str(payload["identity"]["run_timestamp"])[:64]
            if validation["valid"]:
                output["CANDIDATE_CAPTURE_STATUS"] = "PASS"
                return output, 0
            output["CANDIDATE_CAPTURE_STATUS"] = "FAIL"
            output["CAPTURE_WARNING_COUNT"] = min(20, int(validation["error_count"]))
            return output, 1
    except Exception:
        output["CANDIDATE_CAPTURE_STATUS"] = "FAIL"
        output["CAPTURE_WARNING_COUNT"] = 1
        return output, 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print redacted shadow candidate-capture status.")
    parser.add_argument("--database", required=True, help="Explicit local SQLite database path.")
    args = parser.parse_args(argv)
    fields, exit_code = _fields(Path(args.database).expanduser().resolve())
    for name, value in fields.items():
        print(f"{name}={value}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
