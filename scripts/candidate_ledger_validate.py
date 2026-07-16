#!/usr/bin/env python3
"""Validate a candidate ledger using a read-only local SQLite connection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.candidate_ledger.store import CandidateLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only validation for a local offline/research candidate ledger; never accesses trading or network services.",
    )
    parser.add_argument("--database", required=True, help="Explicit local SQLite database file path (no default).")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Offline maintenance mode: exhaustively validate every historical payload and relationship.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with CandidateLedger.open_read_only(args.database) as ledger:
        result = ledger.validate_deep() if args.deep else ledger.validate_quick()
    print(json.dumps({"status": "PASS" if result["valid"] else "FAIL", "VALIDATION_MODE": result["validation_mode"], "read_only": True, "raw_candidates_included": False, **result}, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
