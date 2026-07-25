#!/usr/bin/env python3
"""Operator-only production-auth bridge for one redacted discovery invocation.

With no arguments this command performs a value-free, no-network preflight.
The explicit ``--execute-once`` action is reserved for a separately approved
operator run.
"""

from __future__ import annotations

from pathlib import Path
import requests
import signal
import sys
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.expanded_shadow_discovery_redacted import (  # noqa: E402
    format_discovery_result,
    main as redacted_discovery_main,
    validate_discovery_output,
)
from utils.kalshi import (  # noqa: E402
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
)
from utils.kalshi_discovery_production_bridge import (  # noqa: E402
    FAIL,
    PASS,
    PRIVATE_KEY_SOURCE_TYPE,
    ProductionAuthDiscoveryClient,
    ProductionAuthReport,
    REQUIRED_PRODUCTION_AUTH_FIELD_NAMES,
    build_production_auth_client,
    inspect_production_auth,
)


EXECUTE_ONCE_FLAG = "--execute-once"
MAX_ONE_SHOT_SECONDS = 60
PREFLIGHT_OUTPUT_FIELDS = (
    "PRODUCTION_AUTH_CONTRACT",
    "AUTH_CLIENT_FACTORY_AVAILABLE",
    "REQUIRED_AUTH_FIELD_NAMES",
    "REQUIRED_AUTH_FIELD_COUNT",
    "AUTH_CONFIGURATION_PRESENT",
    "PRIVATE_KEY_SOURCE_TYPE",
    "PRIVATE_KEY_FILE_EXISTS",
    "PRIVATE_KEY_FILE_PERMISSION_STATUS",
    "AUTH_PARSE_STATUS",
    "DIRECT_SECRET_VALUE_OUTPUT",
    "NETWORK_CALL_PERFORMED",
)


class _OneShotTimeout(Exception):
    pass


def format_production_auth_report(report: ProductionAuthReport) -> str:
    lines = (
        f"PRODUCTION_AUTH_CONTRACT={report.contract_status}",
        "AUTH_CLIENT_FACTORY_AVAILABLE="
        f"{'yes' if report.client_factory_available else 'no'}",
        "REQUIRED_AUTH_FIELD_NAMES="
        + ",".join(REQUIRED_PRODUCTION_AUTH_FIELD_NAMES),
        "REQUIRED_AUTH_FIELD_COUNT="
        f"{len(REQUIRED_PRODUCTION_AUTH_FIELD_NAMES)}",
        "AUTH_CONFIGURATION_PRESENT="
        f"{'yes' if report.auth_configuration_present else 'no'}",
        f"PRIVATE_KEY_SOURCE_TYPE={PRIVATE_KEY_SOURCE_TYPE}",
        f"PRIVATE_KEY_FILE_EXISTS={report.private_key_file_exists}",
        "PRIVATE_KEY_FILE_PERMISSION_STATUS="
        f"{report.private_key_file_permission_status}",
        f"AUTH_PARSE_STATUS={report.auth_parse_status}",
        "DIRECT_SECRET_VALUE_OUTPUT=no",
        "NETWORK_CALL_PERFORMED=no",
    )
    return "\n".join(lines) + "\n"


def validate_production_auth_output(output: str) -> bool:
    if len(output.encode("utf-8")) >= 2048 or not output.endswith("\n"):
        return False
    lines = output.splitlines()
    if len(lines) != len(PREFLIGHT_OUTPUT_FIELDS):
        return False
    pairs = [line.split("=", 1) for line in lines if line.count("=") == 1]
    if len(pairs) != len(lines):
        return False
    return tuple(key for key, _value in pairs) == PREFLIGHT_OUTPUT_FIELDS


def _failure_result(outcome: DiscoveryOutcome) -> KalshiDiscoveryResult:
    return KalshiDiscoveryResult(
        outcome=outcome,
        counts=DiscoveryStageCounts(),
        request_status_class=(
            "NETWORK_ERROR"
            if outcome is DiscoveryOutcome.NETWORK_TIMEOUT
            else "NOT_ATTEMPTED"
        ),
    )


def _write_failure_result(outcome: DiscoveryOutcome) -> int:
    output = format_discovery_result(_failure_result(outcome))
    if not validate_discovery_output(output):
        return 1
    sys.stdout.write(output)
    return 1


def _execute_once(
    *,
    client_factory: Callable[[], object],
    environment: Mapping[str, str] | None,
    request_get: Callable,
    timeout_seconds: int,
) -> int:
    bounded_timeout = max(1, min(int(timeout_seconds), MAX_ONE_SHOT_SECONDS))

    def handle_timeout(_signum, _frame) -> None:
        raise _OneShotTimeout

    previous_handler = signal.signal(signal.SIGALRM, handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, bounded_timeout)
    try:
        client = ProductionAuthDiscoveryClient(
            client_factory=client_factory,
            environment=environment,
            request_get=request_get,
        )
        return redacted_discovery_main([], client=client)
    except _OneShotTimeout:
        return _write_failure_result(DiscoveryOutcome.NETWORK_TIMEOUT)
    except Exception:
        return _write_failure_result(
            DiscoveryOutcome.INTERNAL_DISCOVERY_ERROR
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[], object] | None = build_production_auth_client,
    path_exists: Callable[[str], bool] | None = None,
    stat_func: Callable | None = None,
    parse_checker: Callable[[str], bool] | None = None,
    effective_uid: int | None = None,
    request_get: Callable | None = None,
    timeout_seconds: int = MAX_ONE_SHOT_SECONDS,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in ([], [EXECUTE_ONCE_FLAG]):
        return 2

    inspection_kwargs = {
        "client_factory": client_factory,
        "parse_checker": parse_checker,
        "effective_uid": effective_uid,
    }
    if path_exists is not None:
        inspection_kwargs["path_exists"] = path_exists
    if stat_func is not None:
        inspection_kwargs["stat_func"] = stat_func
    report = inspect_production_auth(environment, **inspection_kwargs)

    if not args:
        output = format_production_auth_report(report)
        if not validate_production_auth_output(output):
            return 1
        sys.stdout.write(output)
        if report.contract_status == PASS:
            return 0
        return 1 if report.contract_status == FAIL else 2

    if report.contract_status != PASS or client_factory is None:
        output = format_production_auth_report(report)
        if validate_production_auth_output(output):
            sys.stdout.write(output)
        return 1 if report.contract_status == FAIL else 2

    return _execute_once(
        client_factory=client_factory,
        environment=environment,
        request_get=request_get or requests.get,
        timeout_seconds=timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
