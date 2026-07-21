#!/usr/bin/env python3
"""Read one redacted Expanded Shadow Scanner invocation status and exit."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.candidate_ledger.run_observation import parse_utc, read_run_status


FIELD_ORDER = (
    "EXPANDED_SHADOW_RUN_STATUS",
    "RUN_ID",
    "EXPECTED_SCHEDULED_AT",
    "STARTED_AT",
    "COMPLETED_AT",
    "NORMAL_EXIT_MARKER",
    "EXIT_CODE",
    "CANDIDATES_PROCESSED",
    "TOTAL_MARKETS",
    "CAPTURE_MODE",
    "CANDIDATE_OBSERVED_COUNT",
    "GATE_EVALUATED_COUNT",
    "ORDER_INTENT_COUNT",
    "ORDER_ATTEMPT_COUNT",
    "ORDER_RESULT_COUNT",
    "CAPTURE_WARNING_COUNT",
    "CAPTURE_DROP_COUNT",
    "TYPE_ERROR_PRESENT",
    "TRACEBACK_PRESENT",
    "STALE_STATUS_REJECTED",
    "RUNTIME_SQLITE_ACCESS",
    "RAW_CANDIDATES_INCLUDED",
    "VALUES_PRINTED",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-scheduled-at", required=True)
    parser.add_argument("--max-status-age-seconds", type=int, default=3600)
    parser.add_argument("--schedule-tolerance-seconds", type=int, default=900)
    parser.add_argument("--require-capture-mode", choices=("disabled", "spool"))
    parser.add_argument("--now", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.max_status_age_seconds < 1 or args.schedule_tolerance_seconds < 0:
        parser.error("status age must be positive and schedule tolerance nonnegative")
    now = parse_utc(args.now) if args.now else datetime.now(timezone.utc)
    try:
        fields = read_run_status(
            status_dir=args.status_dir,
            expected_run_id=args.expected_run_id,
            expected_scheduled_at=args.expected_scheduled_at,
            now=now,
            max_status_age_seconds=args.max_status_age_seconds,
            schedule_tolerance_seconds=args.schedule_tolerance_seconds,
            required_capture_mode=args.require_capture_mode,
        )
    except ValueError:
        parser.error("invalid expected run identifier or UTC timestamp")
    for name in FIELD_ORDER:
        print(f"{name}={fields[name]}")
    return {
        "PASS": 0,
        "FAIL": 1,
        "WARN": 2,
        "RUNNING": 2,
        "NOT_STARTED": 3,
    }[str(fields["EXPANDED_SHADOW_RUN_STATUS"])]


if __name__ == "__main__":
    raise SystemExit(main())
