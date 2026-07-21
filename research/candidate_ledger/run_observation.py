"""Atomic, redacted observation records for one Expanded Shadow Scanner run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping


STATUS_VERSION = "expanded-shadow-run-status.v1"
MAX_STATUS_BYTES = 16 * 1024
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

ENV_RUN_ID = "EXPANDED_SHADOW_RUN_ID"
ENV_EXPECTED_AT = "EXPANDED_SHADOW_EXPECTED_SCHEDULED_AT"
ENV_STATUS_DIR = "EXPANDED_SHADOW_RUN_STATUS_DIR"

CAPTURE_FIELDS = {
    "SHADOW_CAPTURE_ENABLED",
    "SHADOW_CAPTURE_WRITTEN_COUNT",
    "SHADOW_CAPTURE_DROPPED_COUNT",
    "SHADOW_CAPTURE_WARNING_COUNT",
    "RUNTIME_CAPTURE_MODE",
    "CANDIDATE_OBSERVED_COUNT",
    "GATE_EVALUATED_COUNT",
    "ORDER_INTENT_COUNT",
    "ORDER_ATTEMPT_COUNT",
    "ORDER_RESULT_COUNT",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def status_path(status_dir: Path, run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("invalid run id")
    return status_dir / f"{run_id}.json"


def _nonnegative(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass
class ExpandedShadowRunObservation:
    """In-memory run state with an optional atomic status-file sink."""

    enabled: bool = False
    run_id: str = ""
    expected_scheduled_at: str = ""
    output_path: Path | None = None
    now: Callable[[], datetime] = utc_now
    configuration_warning_count: int = 0
    started_at: str = ""
    capture: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> "ExpandedShadowRunObservation":
        values = os.environ if environment is None else environment
        run_id = str(values.get(ENV_RUN_ID, "")).strip()
        expected_at = str(values.get(ENV_EXPECTED_AT, "")).strip()
        directory = str(values.get(ENV_STATUS_DIR, "")).strip()
        if not any((run_id, expected_at, directory)):
            return cls(now=now)
        if not all((run_id, expected_at, directory)):
            return cls(now=now, configuration_warning_count=1)
        try:
            if not RUN_ID_PATTERN.fullmatch(run_id):
                raise ValueError("invalid run id")
            expected_at = utc_text(parse_utc(expected_at))
            status_dir = Path(directory).expanduser().resolve()
            status_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
            os.chmod(status_dir, 0o700)
            observation = cls(
                enabled=True,
                run_id=run_id,
                expected_scheduled_at=expected_at,
                output_path=status_path(status_dir, run_id),
                now=now,
            )
            observation.started_at = utc_text(now())
            if not observation._write("STARTED"):
                return cls(now=now, configuration_warning_count=1)
            return observation
        except (OSError, ValueError):
            return cls(now=now, configuration_warning_count=1)

    def record_capture_summary(self, summary: Mapping[str, object]) -> None:
        if not self.enabled:
            return
        self.capture = {name: summary[name] for name in CAPTURE_FIELDS if name in summary}

    def complete(
        self,
        *,
        exit_code: int,
        candidates_processed: int,
        total_markets: int,
    ) -> bool:
        if not self.enabled:
            return self.configuration_warning_count == 0
        return self._write(
            "COMPLETED",
            completed_at=utc_text(self.now()),
            exit_code=int(exit_code),
            candidates_processed=_nonnegative(candidates_processed),
            total_markets=_nonnegative(total_markets),
            normal_exit_marker=int(exit_code) == 0,
        )

    def fail(self, error: Exception) -> bool:
        if not self.enabled:
            return self.configuration_warning_count == 0
        return self._write(
            "FAILED",
            completed_at=utc_text(self.now()),
            exit_code=1,
            candidates_processed=None,
            total_markets=None,
            normal_exit_marker=False,
            type_error_present=isinstance(error, TypeError),
            traceback_present=True,
        )

    def _record(self, state: str, **terminal: object) -> dict[str, object]:
        capture_enabled = str(self.capture.get("SHADOW_CAPTURE_ENABLED", "false")).lower()
        capture_mode = str(self.capture.get("RUNTIME_CAPTURE_MODE", "disabled"))
        if capture_mode not in {"disabled", "spool"}:
            capture_mode = "disabled" if capture_enabled != "true" else "spool"
        record: dict[str, object] = {
            "schema_version": STATUS_VERSION,
            "run_id": self.run_id,
            "expected_scheduled_at": self.expected_scheduled_at,
            "state": state,
            "started_at": self.started_at,
            "completed_at": None,
            "normal_exit_marker": False,
            "exit_code": None,
            "candidates_processed": None,
            "total_markets": None,
            "capture_mode": capture_mode,
            "candidate_observed_count": _nonnegative(self.capture.get("CANDIDATE_OBSERVED_COUNT", 0)),
            "gate_evaluated_count": _nonnegative(self.capture.get("GATE_EVALUATED_COUNT", 0)),
            "order_intent_count": _nonnegative(self.capture.get("ORDER_INTENT_COUNT", 0)),
            "order_attempt_count": _nonnegative(self.capture.get("ORDER_ATTEMPT_COUNT", 0)),
            "order_result_count": _nonnegative(self.capture.get("ORDER_RESULT_COUNT", 0)),
            "capture_warning_count": _nonnegative(self.capture.get("SHADOW_CAPTURE_WARNING_COUNT", 0)),
            "capture_drop_count": _nonnegative(self.capture.get("SHADOW_CAPTURE_DROPPED_COUNT", 0)),
            "capture_written_count": _nonnegative(self.capture.get("SHADOW_CAPTURE_WRITTEN_COUNT", 0)),
            "type_error_present": False,
            "traceback_present": False,
            "runtime_sqlite_access": "none",
            "raw_candidates_included": False,
            "values_printed": "no_secret_values",
        }
        record.update(terminal)
        return record

    def _write(self, state: str, **terminal: object) -> bool:
        if self.output_path is None:
            return False
        record = self._record(state, **terminal)
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_STATUS_BYTES:
            return False
        temporary = self.output_path.with_suffix(".tmp")
        try:
            with temporary.open("wb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.output_path)
            os.chmod(self.output_path, 0o600)
            return True
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False


def read_run_status(
    *,
    status_dir: Path,
    expected_run_id: str,
    expected_scheduled_at: str,
    now: datetime,
    max_status_age_seconds: int,
    schedule_tolerance_seconds: int,
    required_capture_mode: str | None = None,
) -> dict[str, object]:
    """Read exactly one expected invocation record and reject stale attribution."""

    expected_at = utc_text(parse_utc(expected_scheduled_at))
    base: dict[str, object] = {
        "EXPANDED_SHADOW_RUN_STATUS": "NOT_STARTED",
        "RUN_ID": expected_run_id,
        "EXPECTED_SCHEDULED_AT": expected_at,
        "STARTED_AT": "",
        "COMPLETED_AT": "",
        "NORMAL_EXIT_MARKER": "no",
        "EXIT_CODE": "",
        "CANDIDATES_PROCESSED": "",
        "TOTAL_MARKETS": "",
        "CAPTURE_MODE": "",
        "CANDIDATE_OBSERVED_COUNT": 0,
        "GATE_EVALUATED_COUNT": 0,
        "ORDER_INTENT_COUNT": 0,
        "ORDER_ATTEMPT_COUNT": 0,
        "ORDER_RESULT_COUNT": 0,
        "CAPTURE_WARNING_COUNT": 0,
        "CAPTURE_DROP_COUNT": 0,
        "TYPE_ERROR_PRESENT": "no",
        "TRACEBACK_PRESENT": "no",
        "STALE_STATUS_REJECTED": "no",
        "RUNTIME_SQLITE_ACCESS": "none",
        "RAW_CANDIDATES_INCLUDED": "false",
        "VALUES_PRINTED": "no_secret_values",
    }
    path = status_path(status_dir.expanduser().resolve(), expected_run_id)
    if not path.is_file():
        return base
    try:
        if path.stat().st_size > MAX_STATUS_BYTES:
            raise ValueError("oversized status")
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema_version") != STATUS_VERSION:
            raise ValueError("invalid status schema")
        if record.get("run_id") != expected_run_id or record.get("expected_scheduled_at") != expected_at:
            base["EXPANDED_SHADOW_RUN_STATUS"] = "FAIL"
            base["STALE_STATUS_REJECTED"] = "yes"
            return base
        started = parse_utc(str(record["started_at"]))
        if abs((started - parse_utc(expected_at)).total_seconds()) > schedule_tolerance_seconds:
            base["EXPANDED_SHADOW_RUN_STATUS"] = "FAIL"
            base["STALE_STATUS_REJECTED"] = "yes"
            return base
        base.update(
            {
                "STARTED_AT": utc_text(started),
                "COMPLETED_AT": str(record.get("completed_at") or ""),
                "NORMAL_EXIT_MARKER": "yes" if record.get("normal_exit_marker") is True else "no",
                "EXIT_CODE": "" if record.get("exit_code") is None else int(record["exit_code"]),
                "CANDIDATES_PROCESSED": "" if record.get("candidates_processed") is None else _nonnegative(record["candidates_processed"]),
                "TOTAL_MARKETS": "" if record.get("total_markets") is None else _nonnegative(record["total_markets"]),
                "CAPTURE_MODE": str(record.get("capture_mode") or ""),
                "CANDIDATE_OBSERVED_COUNT": _nonnegative(record.get("candidate_observed_count", 0)),
                "GATE_EVALUATED_COUNT": _nonnegative(record.get("gate_evaluated_count", 0)),
                "ORDER_INTENT_COUNT": _nonnegative(record.get("order_intent_count", 0)),
                "ORDER_ATTEMPT_COUNT": _nonnegative(record.get("order_attempt_count", 0)),
                "ORDER_RESULT_COUNT": _nonnegative(record.get("order_result_count", 0)),
                "CAPTURE_WARNING_COUNT": _nonnegative(record.get("capture_warning_count", 0)),
                "CAPTURE_DROP_COUNT": _nonnegative(record.get("capture_drop_count", 0)),
                "TYPE_ERROR_PRESENT": "yes" if record.get("type_error_present") is True else "no",
                "TRACEBACK_PRESENT": "yes" if record.get("traceback_present") is True else "no",
            }
        )
        state = record.get("state")
        completed_text = str(record.get("completed_at") or "")
        reference = parse_utc(completed_text) if completed_text else started
        if (now.astimezone(timezone.utc) - reference).total_seconds() > max_status_age_seconds:
            base["EXPANDED_SHADOW_RUN_STATUS"] = "WARN"
            base["STALE_STATUS_REJECTED"] = "yes"
        elif state == "STARTED":
            base["EXPANDED_SHADOW_RUN_STATUS"] = "RUNNING"
        elif state == "FAILED":
            base["EXPANDED_SHADOW_RUN_STATUS"] = "FAIL"
        elif state == "COMPLETED":
            normal = record.get("normal_exit_marker") is True and record.get("exit_code") == 0
            capture_ok = (
                _nonnegative(record.get("capture_warning_count", 0)) == 0
                and _nonnegative(record.get("capture_drop_count", 0)) == 0
            )
            mode_ok = required_capture_mode is None or record.get("capture_mode") == required_capture_mode
            if not normal:
                base["EXPANDED_SHADOW_RUN_STATUS"] = "FAIL"
            else:
                base["EXPANDED_SHADOW_RUN_STATUS"] = "PASS" if capture_ok and mode_ok else "WARN"
        else:
            base["EXPANDED_SHADOW_RUN_STATUS"] = "FAIL"
        return base
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        base["EXPANDED_SHADOW_RUN_STATUS"] = "FAIL"
        return base
