"""Credential-blind dependency boundary for redacted Kalshi discovery.

This module accepts only an injected discovery client. It does not read
environment variables, open files, load dotenv, parse keys, or perform work at
import time.
"""

from __future__ import annotations

from typing import Protocol

from utils.kalshi import (
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
)


class DiscoveryClient(Protocol):
    """Minimal injected client used by the redacted command."""

    def discover(self) -> KalshiDiscoveryResult:
        """Return one bounded discovery result."""


class AuthenticationUnavailableDiscoveryClient:
    """Fail closed when no privileged client was explicitly injected."""

    __slots__ = ()

    def discover(self) -> KalshiDiscoveryResult:
        return KalshiDiscoveryResult(
            outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
            counts=DiscoveryStageCounts(),
        )
