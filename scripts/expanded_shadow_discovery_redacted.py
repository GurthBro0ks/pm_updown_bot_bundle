#!/usr/bin/env python3
"""Run one bounded read-only discovery and print only approved stage fields."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.kalshi import (  # noqa: E402
    DISCOVERY_ENDPOINT_LABELS,
    REQUEST_STATUS_CLASSES,
    DiscoveryOutcome,
    KalshiDiscoveryResult,
    fetch_kalshi_markets_diagnostic,
)


def _count(value: int | None) -> int:
    return -1 if value is None else max(0, int(value))


def format_discovery_result(result: KalshiDiscoveryResult) -> str:
    """Format a fixed redacted contract with no record-derived values."""

    if result.endpoint_label not in DISCOVERY_ENDPOINT_LABELS:
        endpoint_label = "SERIES_AND_MARKETS"
    else:
        endpoint_label = result.endpoint_label
    request_status_class = (
        result.request_status_class
        if result.request_status_class in REQUEST_STATUS_CLASSES
        else "NETWORK_ERROR"
    )
    if result.outcome in {
        DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
        DiscoveryOutcome.AUTH_REJECTED,
    }:
        auth_check = "FAIL"
    elif result.failure_present:
        auth_check = "WARN"
    else:
        auth_check = "PASS"
    counts = result.counts
    lines = (
        f"AUTH_CHECK={auth_check}",
        f"REQUEST_STATUS_CLASS={request_status_class}",
        f"DISCOVERY_OUTCOME={result.outcome.value}",
        f"PAGE_COUNT={_count(counts.page_count)}",
        f"RAW_RECORD_COUNT={_count(counts.raw_record_count)}",
        f"PARSED_RECORD_COUNT={_count(counts.parsed_record_count)}",
        f"ACTIVE_RECORD_COUNT={_count(counts.active_record_count)}",
        f"CATEGORY_ELIGIBLE_COUNT={_count(counts.category_eligible_count)}",
        f"EXPIRY_ELIGIBLE_COUNT={_count(counts.expiry_eligible_count)}",
        "PRICE_LIQUIDITY_ELIGIBLE_COUNT="
        f"{_count(counts.price_liquidity_eligible_count)}",
        f"FINAL_ELIGIBLE_COUNT={_count(counts.final_eligible_count)}",
        f"ENDPOINT_LABEL={endpoint_label}",
    )
    return "\n".join(lines) + "\n"


def main(
    argv: Sequence[str] | None = None,
    *,
    fetcher: Callable[[], KalshiDiscoveryResult] = fetch_kalshi_markets_diagnostic,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        raise SystemExit("this command accepts no arguments")
    result = fetcher()
    print(format_discovery_result(result), end="")
    return 1 if result.failure_present else 0


if __name__ == "__main__":
    raise SystemExit(main())
