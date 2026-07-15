from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from research.candidate_ledger.models import (
    DuplicateCandidateError,
    DuplicateEventError,
    LeakageError,
    ValidationError,
)
from research.candidate_ledger.store import CandidateLedger


OBSERVED_AT = "2026-01-10T12:00:01Z"


def _append_candidate(ledger, candidate):
    return ledger.append_event(
        candidate_id=candidate["identity"]["candidate_id"],
        event_type="candidate_observed",
        event_timestamp=OBSERVED_AT,
        payload=candidate,
    )


def test_candidate_append_is_transactional_and_idempotent(tmp_path, candidate_factory):
    candidate = candidate_factory()
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        first = _append_candidate(ledger, candidate)
        second = _append_candidate(ledger, candidate)
        assert first.appended is True
        assert second.appended is False
        assert first.event_id == second.event_id
        assert ledger.summary()["candidate_count"] == 1
        assert len(ledger.history(candidate["identity"]["candidate_id"])) == 1


def test_duplicate_candidate_and_conflicting_event_id_fail(tmp_path, candidate_factory):
    candidate = candidate_factory()
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        first = _append_candidate(ledger, candidate)
        with pytest.raises(DuplicateCandidateError):
            ledger.append_event(
                candidate_id=candidate["identity"]["candidate_id"],
                event_type="candidate_observed",
                event_timestamp="2026-01-10T12:00:02Z",
                payload=candidate,
            )
        altered = deepcopy(candidate)
        altered["research"]["review_findings"] = ["different"]
        with pytest.raises(DuplicateEventError):
            ledger.append_event(
                candidate_id=candidate["identity"]["candidate_id"],
                event_type="candidate_observed",
                event_timestamp=OBSERVED_AT,
                payload=altered,
                event_id=first.event_id,
            )


def test_append_only_triggers_reject_update_and_delete(tmp_path, candidate_factory):
    candidate = candidate_factory()
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        _append_candidate(ledger, candidate)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger.connection.execute("UPDATE candidate_events SET event_type='gate_evaluated'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger.connection.execute("DELETE FROM candidate_events")


def test_fill_and_settlement_are_additional_events_and_snapshot_stays_immutable(tmp_path, candidate_factory):
    candidate = candidate_factory()
    original = deepcopy(candidate)
    candidate_id = candidate["identity"]["candidate_id"]
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        _append_candidate(ledger, candidate)
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="fill_observed",
            event_timestamp="2026-01-10T12:01:00Z",
            payload={"fill_status": "partially_filled", "filled_quantity": 1, "average_fill_price": 60, "fees": 0.01},
        )
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="settlement_observed",
            event_timestamp="2026-01-12T12:01:00Z",
            payload={"settlement_result": "yes", "resolved_at": "2026-01-12T12:01:00Z"},
        )
        history = ledger.history(candidate_id)
        assert [event.event_type for event in history] == ["candidate_observed", "fill_observed", "settlement_observed"]
        assert history[0].payload == original
        assert ledger.validate()["valid"] is True


@pytest.mark.parametrize("settlement_result", ["yes", "no"])
def test_yes_and_no_settlement_events_are_bounded(tmp_path, candidate_factory, settlement_result):
    candidate = candidate_factory()
    candidate_id = candidate["identity"]["candidate_id"]
    path = tmp_path / f"ledger-{settlement_result}.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        _append_candidate(ledger, candidate)
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="settlement_observed",
            event_timestamp="2026-01-12T12:01:00Z",
            payload={"settlement_result": settlement_result, "resolved_at": "2026-01-12T12:01:00Z"},
        )
        assert ledger.history(candidate_id)[-1].payload["settlement_result"] == settlement_result


def test_unfilled_maker_and_partial_fill_events_preserve_unknown_fee(tmp_path, candidate_factory):
    candidate = candidate_factory(edge__maker_assumption="maker")
    candidate_id = candidate["identity"]["candidate_id"]
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        _append_candidate(ledger, candidate)
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="fill_observed",
            event_timestamp="2026-01-10T12:01:00Z",
            payload={"fill_status": "unfilled", "filled_quantity": 0, "average_fill_price": None, "fees": None},
        )
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="fill_observed",
            event_timestamp="2026-01-10T12:02:00Z",
            payload={"fill_status": "partially_filled", "filled_quantity": 1, "average_fill_price": 60, "fees": None},
        )
        fills = [event.payload for event in ledger.history(candidate_id) if event.event_type == "fill_observed"]
        assert fills[0]["fill_status"] == "unfilled"
        assert fills[1]["fill_status"] == "partially_filled"
        assert all(fill["fees"] is None for fill in fills)


def test_later_events_require_candidate_and_outer_loop_ground_truth(tmp_path, candidate_factory):
    candidate = candidate_factory()
    candidate_id = candidate["identity"]["candidate_id"]
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        with pytest.raises(ValidationError, match="candidate_observed"):
            ledger.append_event(
                candidate_id=candidate_id,
                event_type="gate_evaluated",
                event_timestamp=OBSERVED_AT,
                payload={"gates": candidate["gates"]},
            )
        _append_candidate(ledger, candidate)
        with pytest.raises(ValidationError, match="prior settlement"):
            ledger.append_event(
                candidate_id=candidate_id,
                event_type="review_recorded",
                event_timestamp="2026-01-10T12:05:00Z",
                payload={
                    "review_score": 1.0,
                    "review_findings": ["synthetic"],
                    "accepted_into_knowledge_base": True,
                    "provenance": "outer_loop_settlement",
                },
            )


def test_settlement_then_outer_loop_review_can_accept_knowledge(tmp_path, candidate_factory):
    candidate = candidate_factory()
    candidate_id = candidate["identity"]["candidate_id"]
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        _append_candidate(ledger, candidate)
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="settlement_observed",
            event_timestamp="2026-01-12T12:01:00Z",
            payload={"settlement_result": "yes", "resolved_at": "2026-01-12T12:01:00Z"},
        )
        result = ledger.append_event(
            candidate_id=candidate_id,
            event_type="review_recorded",
            event_timestamp="2026-01-12T12:02:00Z",
            payload={
                "review_score": 0.9,
                "review_findings": ["synthetic ground truth reviewed"],
                "accepted_into_knowledge_base": True,
                "provenance": "outer_loop_settlement",
            },
        )
        assert result.appended is True


def test_experiment_assignment_is_chronological_and_immutable(tmp_path, candidate_factory):
    candidate = candidate_factory()
    candidate_id = candidate["identity"]["candidate_id"]
    path = tmp_path / "ledger.sqlite3"
    assignment = {
        "experiment_id": "exp-1",
        "split": "train",
        "strategy_version": candidate["identity"]["strategy_version"],
        "assigned_at": "2026-02-01T00:00:00Z",
        "train_end": "2026-01-10T23:59:59Z",
        "validation_end": "2026-01-20T23:59:59Z",
        "holdout_end": "2026-01-31T23:59:59Z",
    }
    with CandidateLedger.initialize(path) as ledger:
        _append_candidate(ledger, candidate)
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="experiment_assignment",
            event_timestamp="2026-02-01T00:00:00Z",
            payload=assignment,
        )
        with pytest.raises(LeakageError, match="append-once"):
            ledger.append_event(
                candidate_id=candidate_id,
                event_type="experiment_assignment",
                event_timestamp="2026-02-01T00:00:01Z",
                payload=assignment,
            )


def test_read_only_connection_cannot_append(tmp_path, candidate_factory):
    path = tmp_path / "ledger.sqlite3"
    with CandidateLedger.initialize(path) as ledger:
        _append_candidate(ledger, candidate_factory())
    with CandidateLedger.open_read_only(path) as ledger:
        with pytest.raises(ValidationError, match="read-only"):
            _append_candidate(ledger, candidate_factory())
