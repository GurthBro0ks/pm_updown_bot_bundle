"""Failure-isolated, disabled-by-default shadow capture buffer."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping

from .models import canonical_json
from .schema import validate_candidate_payload, validate_event_payload
from .store import CandidateLedger, validate_database_path


DEFAULT_CAPTURE_MAX_PER_RUN = 100
DEFAULT_SQLITE_TIMEOUT_MS = 250
MAX_CAPTURE_MAX_PER_RUN = 1000
MAX_SQLITE_TIMEOUT_MS = 2000


@dataclass(frozen=True)
class CaptureConfig:
    enabled: bool = False
    database_path: str | None = None
    max_per_run: int = DEFAULT_CAPTURE_MAX_PER_RUN
    sqlite_timeout_ms: int = DEFAULT_SQLITE_TIMEOUT_MS
    warning_codes: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.enabled and not self.warning_codes and bool(self.database_path)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "CaptureConfig":
        values = os.environ if environment is None else environment
        enabled = str(values.get("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")).strip().lower() == "true"
        database_path = str(values.get("CANDIDATE_LEDGER_DB_PATH", "")).strip() or None
        warnings: list[str] = []

        max_per_run = DEFAULT_CAPTURE_MAX_PER_RUN
        timeout_ms = DEFAULT_SQLITE_TIMEOUT_MS
        if enabled:
            if database_path is None:
                warnings.append("database_path_required")
            try:
                max_per_run = int(
                    str(values.get("CANDIDATE_LEDGER_CAPTURE_MAX_PER_RUN", DEFAULT_CAPTURE_MAX_PER_RUN))
                )
                if not 1 <= max_per_run <= MAX_CAPTURE_MAX_PER_RUN:
                    raise ValueError
            except (TypeError, ValueError):
                max_per_run = DEFAULT_CAPTURE_MAX_PER_RUN
                warnings.append("capture_limit_invalid")
            try:
                timeout_ms = int(
                    str(values.get("CANDIDATE_LEDGER_SQLITE_TIMEOUT_MS", DEFAULT_SQLITE_TIMEOUT_MS))
                )
                if not 1 <= timeout_ms <= MAX_SQLITE_TIMEOUT_MS:
                    raise ValueError
            except (TypeError, ValueError):
                timeout_ms = DEFAULT_SQLITE_TIMEOUT_MS
                warnings.append("sqlite_timeout_invalid")

        return cls(
            enabled=enabled,
            database_path=database_path,
            max_per_run=max_per_run,
            sqlite_timeout_ms=timeout_ms,
            warning_codes=tuple(warnings),
        )


@dataclass(frozen=True)
class CaptureStatus:
    enabled: bool
    attempted: bool = False
    written_count: int = 0
    dropped_count: int = 0
    warning_count: int = 0
    status: str = "DISABLED"
    warning_codes: tuple[str, ...] = ()

    def summary_fields(self) -> dict[str, str | int]:
        return {
            "SHADOW_CAPTURE_ENABLED": str(self.enabled).lower(),
            "SHADOW_CAPTURE_ATTEMPTED": str(self.attempted).lower(),
            "SHADOW_CAPTURE_WRITTEN_COUNT": self.written_count,
            "SHADOW_CAPTURE_DROPPED_COUNT": self.dropped_count,
            "SHADOW_CAPTURE_WARNING_COUNT": self.warning_count,
            "SHADOW_CAPTURE_STATUS": self.status,
        }


class ShadowCaptureBuffer:
    """Collect validated events in memory and flush once, without raising."""

    def __init__(self, config: CaptureConfig):
        self.config = config
        self._events_by_candidate: dict[str, list[dict[str, Any]]] = {}
        self._dropped_count = 0
        self._warning_codes = list(config.warning_codes)
        self._flushed_status: CaptureStatus | None = None

    @property
    def status(self) -> CaptureStatus:
        if self._flushed_status is not None:
            return self._flushed_status
        if not self.config.enabled:
            return CaptureStatus(enabled=False)
        return CaptureStatus(
            enabled=True,
            dropped_count=self._dropped_count,
            warning_count=len(self._warning_codes),
            status="WARN" if self._warning_codes else "READY",
            warning_codes=tuple(self._warning_codes),
        )

    def drop(self, warning_code: str = "malformed_payload") -> None:
        if not self.config.enabled:
            return
        self._dropped_count += 1
        self._warning_codes.append(warning_code)

    def add_candidate(
        self,
        *,
        candidate_payload: Mapping[str, Any],
        event_timestamp: str,
        gate_payload: Mapping[str, Any],
        order_intent_payload: Mapping[str, Any] | None = None,
    ) -> bool:
        if not self.config.enabled:
            return False
        if not self.config.ready:
            self._dropped_count += 1
            return False
        if len(self._events_by_candidate) >= self.config.max_per_run:
            self.drop("capture_limit_reached")
            return False

        try:
            validate_candidate_payload(candidate_payload)
            validate_event_payload("gate_evaluated", gate_payload)
            if order_intent_payload is not None:
                validate_event_payload("order_intent_created", order_intent_payload)
            candidate_id = str(candidate_payload["identity"]["candidate_id"])
            events: list[dict[str, Any]] = [
                {
                    "candidate_id": candidate_id,
                    "event_type": "candidate_observed",
                    "event_timestamp": event_timestamp,
                    "payload": candidate_payload,
                },
                {
                    "candidate_id": candidate_id,
                    "event_type": "gate_evaluated",
                    "event_timestamp": event_timestamp,
                    "payload": gate_payload,
                },
            ]
            if order_intent_payload is not None:
                events.append(
                    {
                        "candidate_id": candidate_id,
                        "event_type": "order_intent_created",
                        "event_timestamp": event_timestamp,
                        "payload": order_intent_payload,
                    }
                )
            existing = self._events_by_candidate.get(candidate_id)
            if existing is not None:
                if canonical_json(existing) == canonical_json(events):
                    return True
                self.drop("candidate_conflict")
                return False
            self._events_by_candidate[candidate_id] = events
            return True
        except Exception:
            self.drop("malformed_payload")
            return False

    def flush(self) -> CaptureStatus:
        if self._flushed_status is not None:
            return self._flushed_status
        if not self.config.enabled:
            self._flushed_status = CaptureStatus(enabled=False)
            return self._flushed_status
        if not self.config.ready:
            self._flushed_status = CaptureStatus(
                enabled=True,
                dropped_count=self._dropped_count,
                warning_count=len(self._warning_codes),
                status="WARN",
                warning_codes=tuple(self._warning_codes),
            )
            return self._flushed_status

        flattened = [
            event
            for candidate_events in self._events_by_candidate.values()
            for event in candidate_events
        ]
        if not flattened:
            self._flushed_status = CaptureStatus(
                enabled=True,
                attempted=False,
                dropped_count=self._dropped_count,
                warning_count=len(self._warning_codes),
                status="PASS",
                warning_codes=tuple(self._warning_codes),
            )
            return self._flushed_status

        try:
            path = validate_database_path(self.config.database_path or "")
            exists = path.exists()
            if not exists:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
            ledger_factory = CandidateLedger if exists else CandidateLedger.initialize
            if exists:
                ledger = ledger_factory(path, timeout_ms=self.config.sqlite_timeout_ms)
            else:
                ledger = ledger_factory(path, timeout_ms=self.config.sqlite_timeout_ms)
            with ledger:
                results = ledger.append_events(flattened)
            written = sum(1 for result in results if result.appended)
            self._flushed_status = CaptureStatus(
                enabled=True,
                attempted=True,
                written_count=written,
                dropped_count=self._dropped_count,
                warning_count=len(self._warning_codes),
                status="PASS" if not self._warning_codes else "WARN",
                warning_codes=tuple(self._warning_codes),
            )
        except Exception:
            warning_codes = tuple([*self._warning_codes, "database_write_failed"])
            self._flushed_status = CaptureStatus(
                enabled=True,
                attempted=True,
                dropped_count=self._dropped_count + len(self._events_by_candidate),
                warning_count=len(warning_codes),
                status="WARN",
                warning_codes=warning_codes,
            )
        return self._flushed_status


def failed_capture_status(*, enabled: bool) -> CaptureStatus:
    """Return a redacted fallback when adapter construction itself fails."""

    return CaptureStatus(
        enabled=enabled,
        warning_count=1,
        status="WARN",
        warning_codes=("adapter_unavailable",),
    )
