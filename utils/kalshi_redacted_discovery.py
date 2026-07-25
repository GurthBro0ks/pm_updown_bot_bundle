"""Secret-isolated credential boundary for redacted Kalshi discovery.

Only inherited process environment values or an injected client are accepted.
This module never opens credential files and never performs work at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Mapping, Protocol

from cryptography.hazmat.primitives import serialization

from utils.kalshi import (
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
    _fetch_kalshi_markets_diagnostic_authenticated,
    get_kalshi_headers,
)
from utils.kalshi_normalize import normalize_kalshi_market


AUTH_KEY_FIELD = "KALSHI_KEY"
AUTH_PRIVATE_KEY_MATERIAL_FIELD = "KALSHI_PRIVATE_KEY_PEM"
REQUIRED_AUTH_FIELD_NAMES = (
    AUTH_KEY_FIELD,
    AUTH_PRIVATE_KEY_MATERIAL_FIELD,
)

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


class DiscoveryClient(Protocol):
    """Minimal injectable client used by the redacted command."""

    def discover(self) -> KalshiDiscoveryResult:
        """Return one bounded discovery result."""


@dataclass(frozen=True)
class AuthRuntimeReport:
    """Value-free inherited-auth preflight result."""

    runtime_context: str
    missing_field_count: int
    parse_status: str


PrivateKeyLoader = Callable[[str], object]


def _load_private_key_material(material: str) -> object:
    return serialization.load_pem_private_key(
        material.encode("utf-8"),
        password=None,
    )


def _resolve_inherited_auth(
    environment: Mapping[str, str],
    *,
    private_key_loader: PrivateKeyLoader,
) -> tuple[AuthRuntimeReport, str | None, object | None]:
    raw_key = environment.get(AUTH_KEY_FIELD)
    raw_private_key = environment.get(AUTH_PRIVATE_KEY_MATERIAL_FIELD)
    missing_field_count = sum(
        value is None or (isinstance(value, str) and not value.strip())
        for value in (raw_key, raw_private_key)
    )
    if missing_field_count:
        return (
            AuthRuntimeReport(
                runtime_context=WARN,
                missing_field_count=missing_field_count,
                parse_status=WARN,
            ),
            None,
            None,
        )
    if not isinstance(raw_key, str) or not isinstance(raw_private_key, str):
        return (
            AuthRuntimeReport(
                runtime_context=FAIL,
                missing_field_count=0,
                parse_status=FAIL,
            ),
            None,
            None,
        )
    try:
        private_key = private_key_loader(raw_private_key)
    except Exception:
        return (
            AuthRuntimeReport(
                runtime_context=FAIL,
                missing_field_count=0,
                parse_status=FAIL,
            ),
            None,
            None,
        )
    return (
        AuthRuntimeReport(
            runtime_context=PASS,
            missing_field_count=0,
            parse_status=PASS,
        ),
        raw_key.strip(),
        private_key,
    )


def inspect_inherited_auth(
    environment: Mapping[str, str] | None = None,
    *,
    private_key_loader: PrivateKeyLoader = _load_private_key_material,
) -> AuthRuntimeReport:
    """Inspect inherited field presence and parseability without any request."""

    values = os.environ if environment is None else environment
    report, _key, _private_key = _resolve_inherited_auth(
        values,
        private_key_loader=private_key_loader,
    )
    return report


class InheritedEnvironmentDiscoveryClient:
    """Resolve inherited credentials at call time, then run one discovery."""

    __slots__ = (
        "_environment",
        "_header_factory",
        "_normalizer",
        "_private_key_loader",
        "_request_get",
    )

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        request_get: Callable | None = None,
        header_factory: Callable = get_kalshi_headers,
        normalizer: Callable[[dict], dict] = normalize_kalshi_market,
        private_key_loader: PrivateKeyLoader = _load_private_key_material,
    ) -> None:
        self._environment = environment
        self._request_get = request_get
        self._header_factory = header_factory
        self._normalizer = normalizer
        self._private_key_loader = private_key_loader

    def discover(self) -> KalshiDiscoveryResult:
        values = os.environ if self._environment is None else self._environment
        report, key, private_key = _resolve_inherited_auth(
            values,
            private_key_loader=self._private_key_loader,
        )
        if report.runtime_context != PASS or key is None or private_key is None:
            return KalshiDiscoveryResult(
                outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
                counts=DiscoveryStageCounts(),
            )
        return _fetch_kalshi_markets_diagnostic_authenticated(
            environment=values,
            api_key=key,
            private_key=private_key,
            request_get=self._request_get,
            header_factory=self._header_factory,
            normalizer=self._normalizer,
        )
