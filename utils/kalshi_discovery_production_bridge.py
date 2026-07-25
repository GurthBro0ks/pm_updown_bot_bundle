"""Privileged production-auth bridge for operator-only redacted discovery.

The normal redacted discovery CLI does not import this module. An operator may
invoke the dedicated launcher to inspect value-free configuration metadata or,
under separate approval, construct the established production client exactly
once and inject its authenticated header boundary into the credential-blind
discovery core.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Callable, Mapping

import requests

from utils.kalshi import (
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
    _fetch_kalshi_markets_diagnostic_with_auth_headers,
)
from utils.kalshi_normalize import normalize_kalshi_market
from utils.kalshi_orders import KalshiOrderClient


PRODUCTION_AUTH_KEY_FIELD = "KALSHI_KEY"
PRODUCTION_AUTH_PRIVATE_KEY_PATH_FIELD = "KALSHI_SECRET_FILE"
REQUIRED_PRODUCTION_AUTH_FIELD_NAMES = (
    PRODUCTION_AUTH_KEY_FIELD,
    PRODUCTION_AUTH_PRIVATE_KEY_PATH_FIELD,
)
DISCOVERY_CONFIGURATION_FIELDS = (
    "KALSHI_BASE_URL",
    "KALSHI_FETCH_INCLUDE_CATEGORIES",
    "KALSHI_FETCH_MIN_LIQUIDITY_USD",
    "KALSHI_SERIES_LIMIT",
)
PRIVATE_KEY_SOURCE_TYPE = "PATH"

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
NOT_RUN = "not_run"
NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ProductionAuthReport:
    """Value-free production-auth preflight result."""

    contract_status: str
    client_factory_available: bool
    auth_configuration_present: bool
    private_key_file_exists: str
    private_key_file_permission_status: str
    auth_parse_status: str


def build_production_auth_client() -> KalshiOrderClient:
    """Construct the established production client without a log-file write."""

    return KalshiOrderClient(log_path=Path(os.devnull))


def _is_present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def inspect_production_auth(
    environment: Mapping[str, str] | None = None,
    *,
    client_factory: Callable[[], object] | None = build_production_auth_client,
    path_exists: Callable[[str], bool] = os.path.isfile,
    stat_func: Callable[[str], os.stat_result] = os.stat,
    parse_checker: Callable[[str], bool] | None = None,
    effective_uid: int | None = None,
) -> ProductionAuthReport:
    """Inspect names, presence, and path metadata without a request or output."""

    values = os.environ if environment is None else environment
    factory_available = callable(client_factory)
    try:
        key_present = _is_present(values.get(PRODUCTION_AUTH_KEY_FIELD))
        raw_path = values.get(PRODUCTION_AUTH_PRIVATE_KEY_PATH_FIELD)
        path_present = _is_present(raw_path)
    except Exception:
        key_present = False
        raw_path = None
        path_present = False
    configuration_present = key_present and path_present

    if not configuration_present or not isinstance(raw_path, str):
        return ProductionAuthReport(
            contract_status=FAIL if not factory_available else WARN,
            client_factory_available=factory_available,
            auth_configuration_present=False,
            private_key_file_exists=NOT_APPLICABLE,
            private_key_file_permission_status=NOT_APPLICABLE,
            auth_parse_status=NOT_RUN,
        )

    try:
        file_exists = bool(path_exists(raw_path))
    except Exception:
        file_exists = False
    if not file_exists:
        return ProductionAuthReport(
            contract_status=FAIL,
            client_factory_available=factory_available,
            auth_configuration_present=True,
            private_key_file_exists="no",
            private_key_file_permission_status=FAIL,
            auth_parse_status=NOT_RUN,
        )

    permission_status = FAIL
    try:
        metadata = stat_func(raw_path)
        expected_uid = os.geteuid() if effective_uid is None else effective_uid
        owner_matches = metadata.st_uid == expected_uid
        owner_only = stat.S_IMODE(metadata.st_mode) & 0o077 == 0
        permission_status = (
            PASS
            if stat.S_ISREG(metadata.st_mode) and owner_matches and owner_only
            else FAIL
        )
    except Exception:
        permission_status = FAIL

    parse_status = NOT_RUN
    if parse_checker is not None and permission_status == PASS:
        try:
            parse_status = PASS if bool(parse_checker(raw_path)) else FAIL
        except Exception:
            parse_status = FAIL

    contract_status = PASS
    if not factory_available or permission_status != PASS or parse_status == FAIL:
        contract_status = FAIL
    return ProductionAuthReport(
        contract_status=contract_status,
        client_factory_available=factory_available,
        auth_configuration_present=True,
        private_key_file_exists="yes",
        private_key_file_permission_status=permission_status,
        auth_parse_status=parse_status,
    )


class ProductionAuthDiscoveryClient:
    """Inject the established production client's read-only auth boundary."""

    __slots__ = (
        "_client_factory",
        "_environment",
        "_normalizer",
        "_request_get",
    )

    def __init__(
        self,
        *,
        client_factory: Callable[[], object] = build_production_auth_client,
        environment: Mapping[str, str] | None = None,
        request_get: Callable = requests.get,
        normalizer: Callable[[dict], dict] = normalize_kalshi_market,
    ) -> None:
        self._client_factory = client_factory
        self._environment = environment
        self._request_get = request_get
        self._normalizer = normalizer

    def discover(self) -> KalshiDiscoveryResult:
        try:
            authenticated_client = self._client_factory()
            auth_headers_factory = getattr(
                authenticated_client,
                "_get_auth_headers",
            )
            if not callable(auth_headers_factory):
                raise TypeError
        except Exception:
            return KalshiDiscoveryResult(
                outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
                counts=DiscoveryStageCounts(),
            )

        source = os.environ if self._environment is None else self._environment
        discovery_configuration = {
            field: str(source.get(field, ""))
            for field in DISCOVERY_CONFIGURATION_FIELDS
            if source.get(field) is not None
        }
        return _fetch_kalshi_markets_diagnostic_with_auth_headers(
            environment=discovery_configuration,
            auth_headers_factory=auth_headers_factory,
            request_get=self._request_get,
            normalizer=self._normalizer,
        )
