#!/usr/bin/env python3
"""Validate inherited discovery authentication without a network request."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.kalshi_redacted_discovery import (  # noqa: E402
    AuthRuntimeReport,
    FAIL,
    PASS,
    REQUIRED_AUTH_FIELD_NAMES,
    inspect_inherited_auth,
)


def format_auth_runtime_report(report: AuthRuntimeReport) -> str:
    lines = (
        f"AUTH_RUNTIME_CONTEXT={report.runtime_context}",
        "REQUIRED_AUTH_FIELD_NAMES=" + ",".join(REQUIRED_AUTH_FIELD_NAMES),
        f"MISSING_AUTH_FIELD_COUNT={max(0, int(report.missing_field_count))}",
        f"AUTH_PARSE_STATUS={report.parse_status}",
        "DIRECT_SECRET_FILE_ACCESS=no",
        "NETWORK_CALL_PERFORMED=no",
    )
    return "\n".join(lines) + "\n"


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    private_key_loader: Callable[[str], object] | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        return 2
    try:
        report = (
            inspect_inherited_auth(environment)
            if private_key_loader is None
            else inspect_inherited_auth(
                environment,
                private_key_loader=private_key_loader,
            )
        )
    except Exception:
        report = AuthRuntimeReport(
            runtime_context=FAIL,
            missing_field_count=len(REQUIRED_AUTH_FIELD_NAMES),
            parse_status=FAIL,
        )
    sys.stdout.write(format_auth_runtime_report(report))
    if report.runtime_context == PASS:
        return 0
    return 1 if report.runtime_context == FAIL else 2


if __name__ == "__main__":
    raise SystemExit(main())
