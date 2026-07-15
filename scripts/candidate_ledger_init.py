#!/usr/bin/env python3
"""Initialize an explicit local candidate-ledger database for offline research."""

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
        description="Initialize a local-only, offline/research candidate ledger. No network or trading access.",
    )
    parser.add_argument("--database", required=True, help="Explicit local SQLite database file path (no default).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with CandidateLedger.initialize(args.database) as ledger:
        result = ledger.validate()
    print(json.dumps({"status": "PASS" if result["valid"] else "FAIL", "offline_research_only": True, **result}, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
