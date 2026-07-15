from __future__ import annotations

from copy import deepcopy

import pytest

from research.candidate_ledger.models import LeakageError, deterministic_candidate_id
from research.candidate_ledger.splits import (
    ExperimentCutoffs,
    chronological_split,
    split_for_timestamp,
    validate_assignment,
    validate_immutable_assignment,
)


CUTOFFS = ExperimentCutoffs(
    train_end="2026-01-10T23:59:59Z",
    validation_end="2026-01-20T23:59:59Z",
    holdout_end="2026-01-31T23:59:59Z",
)


def _at(candidate_factory, timestamp, run_id):
    candidate = candidate_factory(identity__run_timestamp=timestamp, identity__run_id=run_id)
    candidate["prediction"]["source_data_timestamps"] = {"market_snapshot": timestamp}
    candidate["identity"]["candidate_id"] = deterministic_candidate_id(candidate)
    return candidate


def test_chronological_train_validation_holdout(candidate_factory):
    train = _at(candidate_factory, "2026-01-05T12:00:00Z", "train")
    validation = _at(candidate_factory, "2026-01-15T12:00:00Z", "validation")
    holdout = _at(candidate_factory, "2026-01-25T12:00:00Z", "holdout")
    result = chronological_split([holdout, train, validation], CUTOFFS)
    assert [row["identity"]["run_id"] for row in result["train"]] == ["train"]
    assert [row["identity"]["run_id"] for row in result["validation"]] == ["validation"]
    assert [row["identity"]["run_id"] for row in result["holdout"]] == ["holdout"]


def test_no_overlapping_candidate_ids(candidate_factory):
    candidate = _at(candidate_factory, "2026-01-05T12:00:00Z", "same")
    with pytest.raises(LeakageError, match="overlaps"):
        chronological_split([candidate, deepcopy(candidate)], CUTOFFS)


def test_future_record_cannot_be_assigned_to_train(candidate_factory):
    candidate = _at(candidate_factory, "2026-01-25T12:00:00Z", "future")
    with pytest.raises(LeakageError, match="holdout, not train"):
        validate_assignment(
            candidate,
            split="train",
            strategy_version=candidate["identity"]["strategy_version"],
            cutoffs=CUTOFFS,
        )


def test_strategy_provenance_and_future_source_leakage_fail(candidate_factory):
    candidate = _at(candidate_factory, "2026-01-05T12:00:00Z", "provenance")
    with pytest.raises(LeakageError, match="strategy_version"):
        validate_assignment(candidate, split="train", strategy_version="different", cutoffs=CUTOFFS)
    candidate["prediction"]["source_data_timestamps"] = {"market_snapshot": "2026-01-05T12:00:01Z"}
    with pytest.raises(LeakageError, match="later than"):
        validate_assignment(
            candidate,
            split="train",
            strategy_version=candidate["identity"]["strategy_version"],
            cutoffs=CUTOFFS,
        )


def test_future_after_holdout_and_assignment_mutation_fail():
    with pytest.raises(LeakageError, match="later than"):
        split_for_timestamp("2026-02-01T00:00:00Z", CUTOFFS)
    existing = {
        "experiment_id": "exp-1", "split": "train", "strategy_version": "v1",
        "train_end": CUTOFFS.train_end, "validation_end": CUTOFFS.validation_end, "holdout_end": CUTOFFS.holdout_end,
    }
    proposed = {**existing, "split": "holdout"}
    with pytest.raises(LeakageError, match="immutable"):
        validate_immutable_assignment(existing, proposed)
