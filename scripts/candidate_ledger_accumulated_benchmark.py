#!/usr/bin/env python3
"""Run a deterministic, temporary, synthetic accumulated-ledger benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.candidate_ledger.benchmark import DEFAULT_SCALES, OPTIONAL_SCALE, run_benchmark


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark bounded candidate capture against synthetic accumulated SQLite ledgers."
    )
    parser.add_argument("--include-100k", action="store_true")
    parser.add_argument(
        "--scales",
        help="Comma-separated candidate history scales starting with zero (test/tooling override).",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.scales:
        scales = tuple(int(value) for value in args.scales.split(","))
    else:
        scales = (*DEFAULT_SCALES, OPTIONAL_SCALE) if args.include_100k else DEFAULT_SCALES
    with tempfile.TemporaryDirectory(prefix="candidate-ledger-benchmark-") as directory:
        result = run_benchmark(Path(directory), scales=scales, repeats=args.repeats)
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        sys.stdout.write(serialized)
    return 0 if result["classification"] == "PASS_BOUNDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
