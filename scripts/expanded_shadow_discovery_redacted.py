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
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
)
from utils.kalshi_redacted_discovery import (  # noqa: E402
    AuthenticationUnavailableDiscoveryClient,
    DiscoveryClient,
)


OUTPUT_FIELDS = (
    "AUTH_CHECK",
    "REQUEST_STATUS_CLASS",
    "DISCOVERY_OUTCOME",
    "PAGE_COUNT",
    "RAW_RECORD_COUNT",
    "PARSED_RECORD_COUNT",
    "ACTIVE_RECORD_COUNT",
    "CATEGORY_ELIGIBLE_COUNT",
    "EXPIRY_ELIGIBLE_COUNT",
    "PRICE_LIQUIDITY_ELIGIBLE_COUNT",
    "FINAL_ELIGIBLE_COUNT",
    "ENDPOINT_LABEL",
)
COUNT_FIELDS = frozenset(OUTPUT_FIELDS[3:-1])


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


def _internal_error_result() -> KalshiDiscoveryResult:
    return KalshiDiscoveryResult(
        outcome=DiscoveryOutcome.INTERNAL_DISCOVERY_ERROR,
        counts=DiscoveryStageCounts(),
        request_status_class="NOT_ATTEMPTED",
    )


def validate_discovery_output(output: str) -> bool:
    """Reject extra keys, malformed values, and unbounded output."""

    if len(output.encode("utf-8")) >= 2048 or not output.endswith("\n"):
        return False
    lines = output.splitlines()
    if len(lines) != len(OUTPUT_FIELDS):
        return False
    pairs: list[tuple[str, str]] = []
    for line in lines:
        if line.count("=") != 1:
            return False
        key, value = line.split("=", 1)
        pairs.append((key, value))
    if tuple(key for key, _value in pairs) != OUTPUT_FIELDS:
        return False
    values = dict(pairs)
    if values["AUTH_CHECK"] not in {"PASS", "WARN", "FAIL"}:
        return False
    if values["REQUEST_STATUS_CLASS"] not in REQUEST_STATUS_CLASSES:
        return False
    if values["DISCOVERY_OUTCOME"] not in {
        outcome.value for outcome in DiscoveryOutcome
    }:
        return False
    if values["ENDPOINT_LABEL"] not in DISCOVERY_ENDPOINT_LABELS:
        return False
    for field in COUNT_FIELDS:
        value = values[field]
        if value == "-1":
            continue
        if not value.isdigit() or len(value) > 12:
            return False
    return True


def main(
    argv: Sequence[str] | None = None,
    *,
    client: DiscoveryClient | None = None,
    fetcher: Callable[[], KalshiDiscoveryResult] | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        return 2
    if client is not None and fetcher is not None:
        return 2
    try:
        if client is not None:
            result = client.discover()
        elif fetcher is not None:
            result = fetcher()
        else:
            result = AuthenticationUnavailableDiscoveryClient().discover()
        output = format_discovery_result(result)
    except Exception:
        result = _internal_error_result()
        output = format_discovery_result(result)
    if not validate_discovery_output(output):
        result = _internal_error_result()
        output = format_discovery_result(result)
        if not validate_discovery_output(output):
            return 1
    sys.stdout.write(output)
    return 1 if result.failure_present else 0


if __name__ == "__main__":
    raise SystemExit(main())
