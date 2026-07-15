"""Chronological train/validation/holdout assignment and leakage guards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from .models import LeakageError, ValidationError, parse_utc_timestamp
from .schema import validate_source_timestamps


@dataclass(frozen=True)
class ExperimentCutoffs:
    """Pre-registered inclusive UTC cutoffs for chronological splits."""

    train_end: str
    validation_end: str
    holdout_end: str

    def parsed(self) -> tuple[datetime, datetime, datetime]:
        train = parse_utc_timestamp(self.train_end, field="train_end")
        validation = parse_utc_timestamp(self.validation_end, field="validation_end")
        holdout = parse_utc_timestamp(self.holdout_end, field="holdout_end")
        if not train < validation < holdout:
            raise ValidationError("cutoffs must satisfy train_end < validation_end < holdout_end")
        return train, validation, holdout


def split_for_timestamp(timestamp: str, cutoffs: ExperimentCutoffs) -> str:
    """Assign a timestamp without shuffling; reject records after holdout."""

    observed = parse_utc_timestamp(timestamp, field="candidate.run_timestamp")
    train, validation, holdout = cutoffs.parsed()
    if observed <= train:
        return "train"
    if observed <= validation:
        return "validation"
    if observed <= holdout:
        return "holdout"
    raise LeakageError("candidate timestamp is later than the registered holdout window")


def validate_assignment(
    candidate: Mapping[str, Any],
    *,
    split: str,
    strategy_version: str,
    cutoffs: ExperimentCutoffs,
) -> None:
    """Validate split, strategy provenance, and all source timestamps."""

    identity = candidate["identity"]
    expected = split_for_timestamp(identity["run_timestamp"], cutoffs)
    if split != expected:
        raise LeakageError(f"candidate belongs to {expected}, not {split}")
    if strategy_version != identity["strategy_version"]:
        raise LeakageError("assignment strategy_version differs from immutable candidate provenance")
    validate_source_timestamps(candidate)


def chronological_split(
    candidates: Iterable[Mapping[str, Any]],
    cutoffs: ExperimentCutoffs,
) -> dict[str, list[Mapping[str, Any]]]:
    """Return sorted, disjoint chronological cohorts."""

    cohorts: dict[str, list[Mapping[str, Any]]] = {"train": [], "validation": [], "holdout": []}
    seen: set[str] = set()
    ordered = sorted(candidates, key=lambda item: (item["identity"]["run_timestamp"], item["identity"]["candidate_id"]))
    for candidate in ordered:
        candidate_id = candidate["identity"]["candidate_id"]
        if candidate_id in seen:
            raise LeakageError(f"candidate id overlaps experiment splits: {candidate_id}")
        seen.add(candidate_id)
        validate_source_timestamps(candidate)
        split = split_for_timestamp(candidate["identity"]["run_timestamp"], cutoffs)
        cohorts[split].append(candidate)
    return cohorts


def validate_immutable_assignment(
    existing: Mapping[str, Any] | None,
    proposed: Mapping[str, Any],
) -> None:
    """Reject reassignment of one candidate/experiment pair."""

    if existing is None:
        return
    comparable_keys = {"experiment_id", "split", "strategy_version", "train_end", "validation_end", "holdout_end"}
    if any(existing.get(key) != proposed.get(key) for key in comparable_keys):
        raise LeakageError("experiment assignment is immutable")
