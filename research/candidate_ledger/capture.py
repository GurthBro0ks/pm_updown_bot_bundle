"""Failure-isolated, disabled-by-default shadow capture buffer."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping

from .models import canonical_json
from .schema import validate_candidate_payload, validate_event_payload
from .spool import (
    DEFAULT_MAX_BATCH_BYTES,
    DEFAULT_MAX_EVENTS_PER_BATCH,
    DEFAULT_MAX_SPOOL_BATCHES,
    DEFAULT_MAX_SPOOL_BYTES,
    MAX_BATCH_BYTES,
    MAX_EVENTS_PER_BATCH,
    MAX_SPOOL_BATCHES,
    MAX_SPOOL_BYTES,
    SpoolError,
    write_batch,
)


DEFAULT_CAPTURE_MAX_PER_RUN = 100
MAX_CAPTURE_MAX_PER_RUN = 1000


@dataclass(frozen=True)
class CaptureConfig:
    enabled: bool = False
    spool_path: str | None = None
    max_per_run: int = DEFAULT_CAPTURE_MAX_PER_RUN
    max_events_per_batch: int = DEFAULT_MAX_EVENTS_PER_BATCH
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES
    max_spool_bytes: int = DEFAULT_MAX_SPOOL_BYTES
    max_spool_batches: int = DEFAULT_MAX_SPOOL_BATCHES
    warning_codes: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.enabled and not self.warning_codes and bool(self.spool_path)

    @property
    def runtime_mode(self) -> str:
        return "spool" if self.enabled else "disabled"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "CaptureConfig":
        values = os.environ if environment is None else environment
        enabled = str(values.get("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")).strip().lower() == "true"
        spool_path = str(values.get("CANDIDATE_LEDGER_SPOOL_PATH", "")).strip() or None
        warnings: list[str] = []

        max_per_run = DEFAULT_CAPTURE_MAX_PER_RUN
        max_events = DEFAULT_MAX_EVENTS_PER_BATCH
        max_batch_bytes = DEFAULT_MAX_BATCH_BYTES
        max_spool_bytes = DEFAULT_MAX_SPOOL_BYTES
        max_spool_batches = DEFAULT_MAX_SPOOL_BATCHES
        if enabled:
            if spool_path is None:
                warnings.append("spool_path_required")

            def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
                try:
                    result = int(str(values.get(name, default)))
                    if not minimum <= result <= maximum:
                        raise ValueError
                    return result
                except (TypeError, ValueError):
                    warnings.append(f"{name.lower()}_invalid")
                    return default

            max_per_run = bounded_int(
                "CANDIDATE_LEDGER_CAPTURE_MAX_PER_RUN",
                DEFAULT_CAPTURE_MAX_PER_RUN,
                1,
                MAX_CAPTURE_MAX_PER_RUN,
            )
            max_events = bounded_int(
                "CANDIDATE_LEDGER_SPOOL_MAX_EVENTS_PER_BATCH",
                DEFAULT_MAX_EVENTS_PER_BATCH,
                2,
                MAX_EVENTS_PER_BATCH,
            )
            max_batch_bytes = bounded_int(
                "CANDIDATE_LEDGER_SPOOL_MAX_BATCH_BYTES",
                DEFAULT_MAX_BATCH_BYTES,
                4096,
                MAX_BATCH_BYTES,
            )
            max_spool_bytes = bounded_int(
                "CANDIDATE_LEDGER_SPOOL_MAX_BYTES",
                DEFAULT_MAX_SPOOL_BYTES,
                max_batch_bytes,
                MAX_SPOOL_BYTES,
            )
            max_spool_batches = bounded_int(
                "CANDIDATE_LEDGER_SPOOL_MAX_BATCHES",
                DEFAULT_MAX_SPOOL_BATCHES,
                1,
                MAX_SPOOL_BATCHES,
            )
            if max_events < max_per_run * 3:
                warnings.append("spool_event_limit_too_small")

        return cls(
            enabled=enabled,
            spool_path=spool_path,
            max_per_run=max_per_run,
            max_events_per_batch=max_events,
            max_batch_bytes=max_batch_bytes,
            max_spool_bytes=max_spool_bytes,
            max_spool_batches=max_spool_batches,
            warning_codes=tuple(warnings),
        )


@dataclass(frozen=True)
class CaptureStatus:
    enabled: bool
    attempted: bool = False
    written_count: int = 0
    batch_written: bool = False
    batch_bytes: int = 0
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
            "RUNTIME_CAPTURE_MODE": "spool" if self.enabled else "disabled",
            "RUNTIME_BATCH_WRITTEN": str(self.batch_written).lower(),
            "RUNTIME_BATCH_DROPPED": int(self.attempted and not self.batch_written and self.dropped_count > 0),
            "RUNTIME_WARNING_COUNT": self.warning_count,
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
            result = write_batch(
                self.config.spool_path or "",
                flattened,
                max_events=self.config.max_events_per_batch,
                max_batch_bytes=self.config.max_batch_bytes,
                max_spool_bytes=self.config.max_spool_bytes,
                max_spool_batches=self.config.max_spool_batches,
                prevalidated=True,
            )
            self._flushed_status = CaptureStatus(
                enabled=True,
                attempted=True,
                written_count=len(flattened) if result.written else 0,
                batch_written=result.written,
                batch_bytes=result.batch_bytes,
                dropped_count=self._dropped_count,
                warning_count=len(self._warning_codes),
                status="PASS" if not self._warning_codes else "WARN",
                warning_codes=tuple(self._warning_codes),
            )
        except Exception as exc:
            warning_code = "spool_write_failed"
            if isinstance(exc, SpoolError) and "capacity" in str(exc):
                warning_code = "spool_capacity_reached"
            warning_codes = tuple([*self._warning_codes, warning_code])
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
