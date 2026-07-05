#!/usr/bin/env python3
"""Redacted readonly Kalshi health check.

This command intentionally prints only coarse health labels and HTTP status
classes. It never prints env values, key identifiers, key paths, headers, or
response bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests

# Match existing app startup behavior: config loads runtime environment for
# Kalshi clients. This module does not print or expose any loaded values.
import config as _runtime_config  # noqa: F401
from utils.kalshi_orders import KalshiOrderClient


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass(frozen=True)
class EndpointCheck:
    output_key: str
    path: str
    params: Optional[Mapping[str, str]] = None


@dataclass(frozen=True)
class CheckResult:
    output_key: str
    status: str
    http_status_class: str


READONLY_CHECKS = (
    EndpointCheck("KALSHI_AUTH", "/trade-api/v2/markets", {"limit": "1"}),
    EndpointCheck("PORTFOLIO_READONLY", "/trade-api/v2/portfolio/balance"),
    EndpointCheck("OPEN_ORDERS_READONLY", "/trade-api/v2/portfolio/orders", {"status": "resting"}),
)


def _http_status_class(status_code: Optional[int]) -> str:
    if status_code is None:
        return "unknown"
    if 200 <= status_code <= 299:
        return "2xx"
    if 400 <= status_code <= 499:
        return "4xx"
    if 500 <= status_code <= 599:
        return "5xx"
    return "unknown"


def _classify_status(status_code: Optional[int], *, auth_check: bool = False) -> str:
    if status_code is None:
        return WARN
    if 200 <= status_code <= 299:
        return PASS
    if status_code in (401, 403):
        return FAIL if auth_check else WARN
    return WARN


def _overall_status_class(results: list[CheckResult]) -> str:
    classes = {result.http_status_class for result in results}
    if "5xx" in classes:
        return "5xx"
    if "4xx" in classes:
        return "4xx"
    if "unknown" in classes:
        return "unknown"
    return "2xx"


def _run_endpoint_check(
    client: KalshiOrderClient,
    check: EndpointCheck,
    *,
    request_get: Callable[..., object] = requests.get,
    timeout: float = 10.0,
) -> CheckResult:
    path = client._normalize_api_path(check.path)
    try:
        headers = client._get_auth_headers("GET", path)
        response = request_get(
            f"{client.base_url}{path}",
            headers=headers,
            params=dict(check.params or {}),
            timeout=timeout,
        )
        status_code = int(getattr(response, "status_code", 0))
    except requests.Timeout:
        status_code = None
    except requests.RequestException:
        status_code = None
    except Exception:
        status_code = None

    return CheckResult(
        output_key=check.output_key,
        status=_classify_status(status_code, auth_check=check.output_key == "KALSHI_AUTH"),
        http_status_class=_http_status_class(status_code),
    )


def run_checks(
    *,
    client_factory: Callable[[], KalshiOrderClient] = KalshiOrderClient,
    request_get: Callable[..., object] = requests.get,
) -> tuple[list[CheckResult], str]:
    try:
        client = client_factory()
    except Exception:
        results = [
            CheckResult("KALSHI_AUTH", FAIL, "unknown"),
            CheckResult("PORTFOLIO_READONLY", WARN, "unknown"),
            CheckResult("OPEN_ORDERS_READONLY", WARN, "unknown"),
        ]
        return results, _overall_status_class(results)

    results = [
        _run_endpoint_check(client, check, request_get=request_get)
        for check in READONLY_CHECKS
    ]
    return results, _overall_status_class(results)


def format_results(results: list[CheckResult], http_status_class: str) -> str:
    lines = [f"{result.output_key}={result.status}" for result in results]
    lines.append(f"HTTP_STATUS_CLASS={http_status_class}")
    lines.append("VALUES_PRINTED=no")
    return "\n".join(lines) + "\n"


def main() -> int:
    results, http_status_class = run_checks()
    print(format_results(results, http_status_class), end="")
    return 0 if all(result.status == PASS for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
