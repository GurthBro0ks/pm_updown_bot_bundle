"""Shared immutable models and deterministic identifiers for the ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping


SCHEMA_VERSION = "1.0.0"
EVENT_TYPES = (
    "candidate_observed",
    "gate_evaluated",
    "order_intent_created",
    "order_attempted",
    "order_result",
    "fill_observed",
    "settlement_observed",
    "hypothetical_outcome_computed",
    "experiment_assignment",
    "review_recorded",
)


class CandidateLedgerError(Exception):
    """Base class for bounded candidate-ledger errors."""


class ValidationError(CandidateLedgerError):
    """Input does not satisfy the v1 schema or event contract."""


class LeakageError(ValidationError):
    """Chronological or source-data leakage was detected."""


class DuplicateCandidateError(CandidateLedgerError):
    """A second candidate snapshot was attempted for one candidate id."""


class DuplicateEventError(CandidateLedgerError):
    """An event id was reused for different canonical content."""


@dataclass(frozen=True)
class AppendResult:
    """Outcome of an idempotent append."""

    event_id: str
    sequence: int
    appended: bool


@dataclass(frozen=True)
class StoredEvent:
    """Read-only representation of one stored event."""

    sequence: int
    event_id: str
    candidate_id: str
    event_type: str
    event_timestamp: str
    schema_version: str
    payload: Mapping[str, Any]
    payload_sha256: str


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically without accepting non-JSON values."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"value is not canonical JSON: {exc}") from exc


def sha256_text(value: str) -> str:
    """Return a lowercase SHA-256 digest."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_utc_timestamp(value: str, *, field: str = "timestamp") -> datetime:
    """Parse an ISO-8601 timestamp and require an explicit UTC offset."""

    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty UTC timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValidationError(f"{field} must use UTC")
    return parsed.astimezone(timezone.utc)


def utc_now() -> str:
    """Return a canonical UTC timestamp with second precision."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def deterministic_candidate_id(payload: Mapping[str, Any]) -> str:
    """Derive a stable candidate id from immutable public/research identity."""

    try:
        identity = payload["identity"]
        market = payload["market"]
        material = {
            "run_id": identity["run_id"],
            "run_timestamp": identity["run_timestamp"],
            "strategy_version": identity["strategy_version"],
            "git_commit": identity["git_commit"],
            "ticker": market["ticker"],
            "side": market["side"],
            "observed_price_cents": market["observed_price_cents"],
        }
    except (KeyError, TypeError) as exc:
        raise ValidationError("candidate id material is incomplete") from exc
    return f"cand_{sha256_text(canonical_json(material))[:32]}"


def deterministic_event_id(
    *,
    candidate_id: str,
    event_type: str,
    event_timestamp: str,
    payload: Mapping[str, Any],
) -> str:
    """Derive a stable event id from complete canonical event content."""

    material = {
        "candidate_id": candidate_id,
        "event_type": event_type,
        "event_timestamp": event_timestamp,
        "payload": payload,
    }
    return f"evt_{sha256_text(canonical_json(material))[:40]}"
