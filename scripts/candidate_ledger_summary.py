#!/usr/bin/env python3
"""Emit a bounded read-only summary of a local candidate ledger."""

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
        description="Bounded read-only summary for a local offline/research ledger; no raw candidate dump, network, or trading.",
    )
    parser.add_argument("--database", required=True, help="Explicit local SQLite database file path (no default).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with CandidateLedger.open_read_only(args.database) as ledger:
        result = ledger.summary()
    print(json.dumps({"status": "PASS", "read_only": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
