from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.candidate_ledger.models import LeakageError, ValidationError, deterministic_candidate_id
from research.candidate_ledger.schema import validate_candidate_payload, validate_event_payload


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas/candidate_ledger/v1.schema.json"


def test_versioned_schema_is_closed_and_covers_required_groups():
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["$schema"].endswith("draft/2020-12/schema")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "identity", "market", "prediction", "edge", "gates", "decision", "outcome", "research"
    }
    assert schema["properties"]["identity"]["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["properties"]["decision"]["properties"]["internal_order_reference"]


def test_valid_synthetic_candidate_and_deterministic_id(candidate_factory):
    candidate = candidate_factory()
    validate_candidate_payload(candidate)
    assert candidate["identity"]["candidate_id"] == deterministic_candidate_id(deepcopy(candidate))


def test_rejected_candidate_is_valid_but_later_outcome_in_observation_fails(candidate_factory):
    rejected = candidate_factory(
        gates__edge_pass=False,
        gates__profitability_pass=False,
        gates__gate_failure_kinds=["fee_adjusted_edge_below_threshold"],
        decision__rejection_reason="fee_adjusted_edge_below_threshold",
        decision__order_intent_created=False,
        decision__order_attempted=False,
        decision__order_placed=False,
        decision__internal_order_reference=None,
        decision__quantity=None,
        decision__limit_price_cents=None,
    )
    validate_event_payload("candidate_observed", rejected)
    rejected["outcome"]["settlement_result"] = "no"
    rejected["outcome"]["resolved_at"] = "2026-01-12T12:01:00Z"
    with pytest.raises(LeakageError, match="later fill, settlement"):
        validate_event_payload("candidate_observed", rejected)


def test_malformed_and_unsupported_schema_fail(candidate_factory):
    malformed = candidate_factory()
    del malformed["market"]["expiry"]
    with pytest.raises(ValidationError, match="missing"):
        validate_candidate_payload(malformed)

    unsupported = candidate_factory(identity__schema_version="2.0.0")
    with pytest.raises(ValidationError, match="unsupported schema_version"):
        validate_candidate_payload(unsupported)


def test_secret_bearing_field_and_raw_prompt_field_fail(candidate_factory):
    candidate = candidate_factory()
    candidate["prediction"]["raw_prompt"] = "synthetic text"
    with pytest.raises(ValidationError, match="secret-bearing field"):
        validate_candidate_payload(candidate)


def test_future_source_timestamp_fails_leakage_guard(candidate_factory):
    candidate = candidate_factory(
        prediction__source_data_timestamps={"market_snapshot": "2026-01-10T12:00:01Z"}
    )
    with pytest.raises(LeakageError, match="later than"):
        validate_candidate_payload(candidate)


def test_unknown_fee_must_keep_fee_adjusted_values_unknown(candidate_factory):
    candidate = candidate_factory(
        edge__estimated_fees=None,
        edge__fee_adjusted_edge=None,
        edge__expected_value=None,
    )
    validate_candidate_payload(candidate)
    candidate["edge"]["fee_adjusted_edge"] = 0.1
    with pytest.raises(ValidationError, match="fee-adjusted fields"):
        validate_candidate_payload(candidate)


def test_unknown_event_type_fails(candidate_factory):
    with pytest.raises(ValidationError, match="unsupported event_type"):
        validate_event_payload("invented_event", candidate_factory())


def test_all_bounded_event_payload_types_validate(candidate_factory):
    candidate = candidate_factory()
    payloads = {
        "candidate_observed": candidate,
        "gate_evaluated": {"gates": candidate["gates"]},
        "order_intent_created": {"internal_order_reference": "internal-1", "quantity": 1, "limit_price_cents": 60},
        "order_attempted": {"internal_order_reference": "internal-1"},
        "order_result": {"internal_order_reference": "internal-1", "placed": False, "rejection_reason": "synthetic"},
        "fill_observed": {"fill_status": "unfilled", "filled_quantity": 0, "average_fill_price": None, "fees": None},
        "settlement_observed": {"settlement_result": "yes", "resolved_at": "2026-01-12T12:01:00Z"},
        "hypothetical_outcome_computed": {"hypothetical_pnl_if_traded": None, "estimated_fill_feasibility": 0.5, "fees": None},
        "experiment_assignment": {
            "experiment_id": "exp-1", "split": "train", "strategy_version": "synthetic-strategy@v1",
            "assigned_at": "2026-02-01T00:00:00Z", "train_end": "2026-01-10T23:59:59Z",
            "validation_end": "2026-01-20T23:59:59Z", "holdout_end": "2026-01-31T23:59:59Z",
        },
        "review_recorded": {
            "review_score": None, "review_findings": [], "accepted_into_knowledge_base": False,
            "provenance": "offline_replay",
        },
    }
    for event_type, payload in payloads.items():
        validate_event_payload(event_type, payload)
