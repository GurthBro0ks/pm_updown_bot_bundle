#!/usr/bin/env python3
"""Print redacted nearest-miss edge diagnostics for the latest main run.

This is read-only. It reads only the structured redacted summary produced by
future main runs; it does not parse raw logs or inspect credentials.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.edge_nearest_miss import format_summary, load_summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print redacted nearest-miss edge summary.")
    parser.add_argument(
        "--summary",
        default=None,
        help="Structured redacted nearest-miss summary path. Defaults to logs/main_edge_nearest_miss_latest.json.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary_path = Path(args.summary) if args.summary else None
    summary = load_summary(summary_path)
    print(format_summary(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
