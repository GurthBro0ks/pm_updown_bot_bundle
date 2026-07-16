"""Bounded, immutable local spool batches for observational capture."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping
import zlib

from .models import (
    ValidationError,
    canonical_json,
    deterministic_event_id,
    sha256_text,
)
from .schema import validate_candidate_payload, validate_event_payload


SPOOL_VERSION = "candidate-capture-spool.v1"
SPOOL_SUFFIX = ".clspool"
DEFAULT_MAX_EVENTS_PER_BATCH = 300
DEFAULT_MAX_BATCH_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_SPOOL_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_SPOOL_BATCHES = 10_000
MAX_EVENTS_PER_BATCH = 3_000
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_SPOOL_BYTES = 1024 * 1024 * 1024
MAX_SPOOL_BATCHES = 100_000


class SpoolError(ValidationError):
    """A spool path, batch, or capacity contract was violated."""


@dataclass(frozen=True)
class SpoolWriteResult:
    batch_id: str
    batch_path: Path
    batch_bytes: int
    written: bool


@dataclass(frozen=True)
class SpoolScanResult:
    batch_paths: tuple[Path, ...]
    batch_count: int
    total_bytes: int


def validate_spool_path(spool_path: str | Path) -> Path:
    """Require an explicit, existing, non-symlink local directory."""

    raw = str(spool_path)
    if not raw or raw.startswith("file:") or "://" in raw:
        raise SpoolError("an explicit local spool directory is required")
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise SpoolError("spool directory must not be a symlink")
    path = path.resolve()
    if not path.is_dir():
        raise SpoolError("spool directory does not exist")
    return path


def _prepared_events(
    events: Iterable[Mapping[str, Any]], *, validate_payloads: bool = True
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for source in events:
        candidate_id = str(source["candidate_id"])
        event_type = str(source["event_type"])
        event_timestamp = str(source["event_timestamp"])
        payload = source["payload"]
        if validate_payloads:
            validate_event_payload(event_type, payload)
            if event_type == "candidate_observed":
                validate_candidate_payload(payload)
        event_id = str(
            source.get("event_id")
            or deterministic_event_id(
                candidate_id=candidate_id,
                event_type=event_type,
                event_timestamp=event_timestamp,
                payload=payload,
            )
        )
        if not event_id.startswith("evt_") or len(event_id) > 128:
            raise SpoolError("event_id must be a bounded evt_ identifier")
        prepared.append(
            {
                "candidate_id": candidate_id,
                "event_id": event_id,
                "event_timestamp": event_timestamp,
                "event_type": event_type,
                "payload": payload,
            }
        )
    return prepared


def serialize_batch(
    events: Iterable[Mapping[str, Any]],
    *,
    max_events: int = DEFAULT_MAX_EVENTS_PER_BATCH,
    max_bytes: int = DEFAULT_MAX_BATCH_BYTES,
    prevalidated: bool = False,
) -> tuple[str, bytes]:
    """Validate and frame one deterministic immutable spool batch."""

    prepared = _prepared_events(events, validate_payloads=not prevalidated)
    if not prepared or len(prepared) > max_events:
        raise SpoolError("spool batch event count is outside the bounded range")
    body = {"events": prepared, "spool_version": SPOOL_VERSION}
    body_json = canonical_json(body)
    body_sha256 = sha256_text(body_json)
    batch_id = f"batch_{body_sha256[:40]}"
    compressed = zlib.compress(body_json.encode("utf-8"), level=6)
    envelope = {
        "batch_id": batch_id,
        "body_encoding": "zlib+base64",
        "body_sha256": body_sha256,
        "body_zlib": base64.b64encode(compressed).decode("ascii"),
        "event_count": len(prepared),
        "spool_version": SPOOL_VERSION,
    }
    serialized = (canonical_json(envelope) + "\n").encode("utf-8")
    if len(serialized) > max_bytes:
        raise SpoolError("spool batch exceeds the serialized byte limit")
    return batch_id, serialized


def parse_batch_bytes(
    serialized: bytes,
    *,
    max_events: int = DEFAULT_MAX_EVENTS_PER_BATCH,
    max_bytes: int = DEFAULT_MAX_BATCH_BYTES,
) -> dict[str, Any]:
    """Verify bounded framing, version, checksum, identity, and event schemas."""

    if not serialized or len(serialized) > max_bytes or not serialized.endswith(b"\n"):
        raise SpoolError("spool record is empty, oversized, or truncated")
    try:
        envelope = json.loads(serialized.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpoolError("spool record is malformed or truncated") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "batch_id",
        "body_encoding",
        "body_sha256",
        "body_zlib",
        "event_count",
        "spool_version",
    }:
        raise SpoolError("spool envelope fields are invalid")
    if envelope["spool_version"] != SPOOL_VERSION:
        raise SpoolError("spool version is unsupported")
    if envelope["body_encoding"] != "zlib+base64":
        raise SpoolError("spool body encoding is unsupported")
    try:
        compressed = base64.b64decode(envelope["body_zlib"], validate=True)
        decompressor = zlib.decompressobj()
        body_bytes = decompressor.decompress(compressed, max_bytes + 1)
        if len(body_bytes) > max_bytes or decompressor.unconsumed_tail:
            raise SpoolError("spool compressed body is truncated or oversized")
        body_bytes += decompressor.flush(max_bytes + 1 - len(body_bytes))
        if not decompressor.eof or decompressor.unused_data or len(body_bytes) > max_bytes:
            raise SpoolError("spool compressed body is truncated or oversized")
        body = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, zlib.error) as exc:
        raise SpoolError("spool compressed body is malformed or truncated") from exc
    if not isinstance(body, dict) or set(body) != {"events", "spool_version"}:
        raise SpoolError("spool body fields are invalid")
    if body["spool_version"] != SPOOL_VERSION:
        raise SpoolError("spool version is unsupported")
    events = body["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= max_events:
        raise SpoolError("spool event count is outside the bounded range")
    if envelope["event_count"] != len(events):
        raise SpoolError("spool event count does not match framing")
    body_json = canonical_json(body)
    body_sha256 = sha256_text(body_json)
    if envelope["body_sha256"] != body_sha256:
        raise SpoolError("spool checksum mismatch")
    expected_batch_id = f"batch_{body_sha256[:40]}"
    if envelope["batch_id"] != expected_batch_id:
        raise SpoolError("spool batch id conflicts with canonical content")
    prepared = _prepared_events(events)
    if canonical_json(prepared) != canonical_json(events):
        raise SpoolError("spool event fields are invalid")
    envelope["body"] = body
    return envelope


def read_batch(path: Path, *, max_bytes: int = DEFAULT_MAX_BATCH_BYTES) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise SpoolError("spool batch path must be a regular non-symlink file")
    size = path.stat().st_size
    if size < 1 or size > max_bytes:
        raise SpoolError("spool batch size is outside the bounded range")
    with path.open("rb") as handle:
        serialized = handle.read(max_bytes + 1)
    return parse_batch_bytes(serialized, max_bytes=max_bytes)


def scan_spool(spool_path: str | Path, *, max_batches: int = MAX_SPOOL_BATCHES) -> SpoolScanResult:
    path = validate_spool_path(spool_path)
    batch_paths: list[Path] = []
    total_bytes = 0
    with os.scandir(path) as entries:
        for entry in entries:
            if not entry.name.endswith(SPOOL_SUFFIX):
                continue
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise SpoolError("spool contains a non-regular batch entry")
            batch_paths.append(path / entry.name)
            total_bytes += entry.stat(follow_symlinks=False).st_size
            if len(batch_paths) > max_batches:
                raise SpoolError("spool batch count exceeds the bounded scan limit")
    batch_paths.sort(key=lambda item: item.name)
    return SpoolScanResult(tuple(batch_paths), len(batch_paths), total_bytes)


def write_batch(
    spool_path: str | Path,
    events: Iterable[Mapping[str, Any]],
    *,
    max_events: int = DEFAULT_MAX_EVENTS_PER_BATCH,
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
    max_spool_bytes: int = DEFAULT_MAX_SPOOL_BYTES,
    max_spool_batches: int = DEFAULT_MAX_SPOOL_BATCHES,
    prevalidated: bool = False,
) -> SpoolWriteResult:
    """Atomically publish one immutable batch under a deterministic filename."""

    path = validate_spool_path(spool_path)
    batch_id, serialized = serialize_batch(
        events,
        max_events=max_events,
        max_bytes=max_batch_bytes,
        prevalidated=prevalidated,
    )
    target = path / f"{batch_id}{SPOOL_SUFFIX}"
    lock_descriptor = os.open(path / ".candidate-capture.lock", os.O_CREAT | os.O_RDWR, 0o600)
    temporary_name: str | None = None
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        if target.exists():
            if target.stat().st_size > max_batch_bytes:
                raise SpoolError("existing spool batch exceeds the byte limit")
            with target.open("rb") as handle:
                existing = handle.read(max_batch_bytes + 1)
            parse_batch_bytes(existing, max_events=max_events, max_bytes=max_batch_bytes)
            if existing != serialized:
                raise SpoolError("existing batch id has conflicting content")
            return SpoolWriteResult(batch_id, target, len(serialized), False)
        scan = scan_spool(path, max_batches=max_spool_batches)
        if scan.batch_count >= max_spool_batches:
            raise SpoolError("spool batch capacity reached")
        if scan.total_bytes + len(serialized) > max_spool_bytes:
            raise SpoolError("spool byte capacity reached")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{batch_id}.", suffix=".tmp", dir=path
        )
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(serialized):
                written = os.write(descriptor, serialized[offset:])
                if written <= 0:
                    raise SpoolError("spool write was incomplete")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary_name, target)
        except FileExistsError:
            if target.stat().st_size > max_batch_bytes:
                raise SpoolError("existing spool batch exceeds the byte limit")
            with target.open("rb") as handle:
                existing = handle.read(max_batch_bytes + 1)
            parse_batch_bytes(existing, max_events=max_events, max_bytes=max_batch_bytes)
            if existing != serialized:
                raise SpoolError("existing batch id has conflicting content")
            return SpoolWriteResult(batch_id, target, len(serialized), False)
        os.unlink(temporary_name)
        temporary_name = None
        directory_descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return SpoolWriteResult(batch_id, target, len(serialized), True)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        os.close(lock_descriptor)
