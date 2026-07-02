#!/usr/bin/env python3
"""Secret-safe proof snapshot comparison helper."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import sys
from pathlib import Path


def normalized_rows(path: Path, *, ignore_headers: bool = True) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows = text.splitlines()
    if not ignore_headers:
        return rows
    return [row for row in rows if not row.startswith("===")]


def normalized_sha256(rows: list[str]) -> str:
    payload = "\n".join(rows).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare proof files after normalization. By default, decorative "
            "proof headers beginning with === are ignored and raw content is "
            "not printed."
        )
    )
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument(
        "--include-headers",
        action="store_true",
        help="Include decorative === header rows in the comparison.",
    )
    parser.add_argument(
        "--show-diff",
        action="store_true",
        help="Print a raw unified diff of normalized rows. Do not use for secret-bearing proofs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ignore_headers = not args.include_headers

    left_rows = normalized_rows(args.left, ignore_headers=ignore_headers)
    right_rows = normalized_rows(args.right, ignore_headers=ignore_headers)
    left_sha = normalized_sha256(left_rows)
    right_sha = normalized_sha256(right_rows)
    matched = left_rows == right_rows

    print(f"PROOF_COMPARE_RESULT={'PASS' if matched else 'FAIL'}")
    print(f"LEFT_NORMALIZED_SHA256={left_sha}")
    print(f"RIGHT_NORMALIZED_SHA256={right_sha}")
    print(f"LEFT_ROWS={len(left_rows)}")
    print(f"RIGHT_ROWS={len(right_rows)}")

    if args.show_diff and not matched:
        diff = difflib.unified_diff(
            left_rows,
            right_rows,
            fromfile=str(args.left),
            tofile=str(args.right),
            lineterm="",
        )
        for row in diff:
            print(row)

    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
