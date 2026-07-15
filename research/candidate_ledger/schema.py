"""Strict standard-library validation for candidate snapshots and events."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from .models import (
    EVENT_TYPES,
    SCHEMA_VERSION,
    LeakageError,
    ValidationError,
    deterministic_candidate_id,
    parse_utc_timestamp,
)


_FORBIDDEN_KEYS = {
    "account_id",
    "api_key",
    "auth_header",
    "authorization",
    "completion",
    "credential",
    "credentials",
    "password",
    "private_account_identifier",
    "private_key",
    "prompt",
    "raw_completion",
    "raw_external_response",
    "raw_prompt",
    "raw_response",
    "secret",
    "shell_history",
    "token",
    "webhook",
    "webhook_url",
}
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER_MATERIAL = re.compile(r"(?i)\bauthorization\s*:\s*bearer\b|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_WEBHOOK_ENDPOINT = re.compile(r"(?i)https://(?:discord(?:app)?\.com)/api/webhooks/")
_CANDIDATE_ID = re.compile(r"^cand_[0-9a-f]{32}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


ROOT_KEYS = {"identity", "market", "prediction", "edge", "gates", "decision", "outcome", "research"}
IDENTITY_KEYS = {"schema_version", "candidate_id", "run_id", "run_timestamp", "strategy_version", "git_commit", "environment_mode"}
MARKET_KEYS = {"ticker", "series", "category", "public_title", "expiry", "settlement_rule_reference", "side", "observed_price_cents", "bid_cents", "ask_cents", "spread_cents"}
PREDICTION_KEYS = {"provider", "model", "policy_version", "ai_prior", "fallback_prior_used", "confidence", "source_data_timestamps"}
CONFIDENCE_KEYS = {"confidence_bucket", "self_reported_confidence"}
EDGE_KEYS = {"raw_edge", "fee_adjusted_edge", "required_threshold", "estimated_fees", "maker_assumption", "expected_value"}
GATE_KEYS = {"expiry_pass", "category_pass", "weather_exclusion_pass", "price_pass", "edge_pass", "profitability_pass", "fallback_prior_pass", "liquidity_pass", "market_end_time_pass", "price_sanity_pass", "size_pass", "bankroll_pass", "notional_pass", "duplicate_pass", "open_order_pass", "gate_failure_kinds"}
DECISION_KEYS = {"rejection_reason", "order_intent_created", "order_attempted", "order_placed", "internal_order_reference", "quantity", "limit_price_cents"}
OUTCOME_KEYS = {"fill_status", "filled_quantity", "average_fill_price", "settlement_result", "resolved_at", "realized_pnl", "hypothetical_pnl_if_traded", "estimated_fill_feasibility", "brier_score", "log_loss", "calibration_bucket"}
RESEARCH_KEYS = {"experiment_id", "train_or_holdout", "replay_policy_version", "review_score", "review_findings", "accepted_into_knowledge_base", "provenance", "immutable_created_at"}


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ValidationError(f"{field} keys invalid; missing={missing}, extra={extra}")


def _string(value: Any, field: str, *, nullable: bool = False, max_length: int = 512) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValidationError(f"{field} must be a bounded non-empty string")


def _bool(value: Any, field: str) -> None:
    if type(value) is not bool:
        raise ValidationError(f"{field} must be boolean")


def _number(
    value: Any,
    field: str,
    *,
    nullable: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{field} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValidationError(f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{field} must be <= {maximum}")


def _integer(
    value: Any,
    field: str,
    *,
    nullable: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
) -> None:
    if nullable and value is None:
        return
    if type(value) is not int:
        raise ValidationError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValidationError(f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{field} must be <= {maximum}")


def reject_secret_material(value: Any, *, path: str = "payload") -> None:
    """Reject secret-bearing keys and unmistakable secret material recursively."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_KEYS:
                raise ValidationError(f"secret-bearing field is prohibited at {path}.{key}")
            reject_secret_material(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_secret_material(nested, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if _PRIVATE_KEY_BLOCK.search(value) or _BEARER_MATERIAL.search(value) or _WEBHOOK_ENDPOINT.search(value):
            raise ValidationError(f"secret-bearing value is prohibited at {path}")


def validate_source_timestamps(payload: Mapping[str, Any]) -> None:
    """Reject source data newer than candidate observation time."""

    run_time = parse_utc_timestamp(payload["identity"]["run_timestamp"], field="identity.run_timestamp")
    timestamps = _mapping(payload["prediction"]["source_data_timestamps"], "prediction.source_data_timestamps")
    if not timestamps:
        raise ValidationError("prediction.source_data_timestamps must not be empty")
    for name, raw in timestamps.items():
        if str(name).lower() in _FORBIDDEN_KEYS:
            raise ValidationError(f"secret-bearing source timestamp name is prohibited: {name}")
        source_time = parse_utc_timestamp(raw, field=f"prediction.source_data_timestamps.{name}")
        if source_time > run_time:
            raise LeakageError(f"source timestamp {name} is later than candidate run_timestamp")


def validate_candidate_payload(payload: Mapping[str, Any]) -> None:
    """Validate one complete immutable v1 candidate snapshot."""

    root = _mapping(payload, "candidate")
    reject_secret_material(root)
    _exact_keys(root, ROOT_KEYS, "candidate")

    identity = _mapping(root["identity"], "identity")
    _exact_keys(identity, IDENTITY_KEYS, "identity")
    if identity["schema_version"] != SCHEMA_VERSION:
        raise ValidationError(f"unsupported schema_version: {identity['schema_version']!r}")
    _string(identity["candidate_id"], "identity.candidate_id", max_length=37)
    if not _CANDIDATE_ID.fullmatch(identity["candidate_id"]):
        raise ValidationError("identity.candidate_id has invalid format")
    _string(identity["run_id"], "identity.run_id", max_length=128)
    parse_utc_timestamp(identity["run_timestamp"], field="identity.run_timestamp")
    _string(identity["strategy_version"], "identity.strategy_version", max_length=128)
    if not isinstance(identity["git_commit"], str) or not _GIT_COMMIT.fullmatch(identity["git_commit"]):
        raise ValidationError("identity.git_commit has invalid format")
    if identity["environment_mode"] not in {"dry_run", "micro_live", "live", "replay_offline"}:
        raise ValidationError("identity.environment_mode is unsupported")

    market = _mapping(root["market"], "market")
    _exact_keys(market, MARKET_KEYS, "market")
    for field in ("ticker", "series", "category", "public_title", "settlement_rule_reference"):
        _string(market[field], f"market.{field}")
    parse_utc_timestamp(market["expiry"], field="market.expiry")
    if market["side"] not in {"yes", "no"}:
        raise ValidationError("market.side must be yes or no")
    _integer(market["observed_price_cents"], "market.observed_price_cents", minimum=1, maximum=99)
    for field in ("bid_cents", "ask_cents", "spread_cents"):
        _integer(market[field], f"market.{field}", nullable=True, minimum=0, maximum=100)

    prediction = _mapping(root["prediction"], "prediction")
    _exact_keys(prediction, PREDICTION_KEYS, "prediction")
    for field in ("provider", "model", "policy_version"):
        _string(prediction[field], f"prediction.{field}", max_length=128)
    _number(prediction["ai_prior"], "prediction.ai_prior", minimum=0, maximum=1)
    _bool(prediction["fallback_prior_used"], "prediction.fallback_prior_used")
    confidence = _mapping(prediction["confidence"], "prediction.confidence")
    _exact_keys(confidence, CONFIDENCE_KEYS, "prediction.confidence")
    if confidence["confidence_bucket"] not in {"low", "medium", "high", "unknown"}:
        raise ValidationError("prediction.confidence.confidence_bucket is unsupported")
    _number(confidence["self_reported_confidence"], "prediction.confidence.self_reported_confidence", nullable=True, minimum=0, maximum=1)
    validate_source_timestamps(root)

    edge = _mapping(root["edge"], "edge")
    _exact_keys(edge, EDGE_KEYS, "edge")
    _number(edge["raw_edge"], "edge.raw_edge")
    _number(edge["fee_adjusted_edge"], "edge.fee_adjusted_edge", nullable=True)
    _number(edge["required_threshold"], "edge.required_threshold")
    _number(edge["estimated_fees"], "edge.estimated_fees", nullable=True, minimum=0)
    if edge["maker_assumption"] not in {"maker", "taker", "unknown"}:
        raise ValidationError("edge.maker_assumption is unsupported")
    _number(edge["expected_value"], "edge.expected_value", nullable=True)
    if edge["estimated_fees"] is None and (edge["fee_adjusted_edge"] is not None or edge["expected_value"] is not None):
        raise ValidationError("fee-adjusted fields must be unknown when estimated_fees is unknown")

    gates = _mapping(root["gates"], "gates")
    _exact_keys(gates, GATE_KEYS, "gates")
    for field in GATE_KEYS - {"gate_failure_kinds"}:
        _bool(gates[field], f"gates.{field}")
    failures = gates["gate_failure_kinds"]
    if not isinstance(failures, list) or len(failures) > 32 or len(failures) != len(set(failures)):
        raise ValidationError("gates.gate_failure_kinds must be a bounded unique list")
    for item in failures:
        _string(item, "gates.gate_failure_kinds[]", max_length=128)

    decision = _mapping(root["decision"], "decision")
    _exact_keys(decision, DECISION_KEYS, "decision")
    _string(decision["rejection_reason"], "decision.rejection_reason", nullable=True, max_length=256)
    for field in ("order_intent_created", "order_attempted", "order_placed"):
        _bool(decision[field], f"decision.{field}")
    _string(decision["internal_order_reference"], "decision.internal_order_reference", nullable=True, max_length=128)
    _integer(decision["quantity"], "decision.quantity", nullable=True, minimum=0)
    _integer(decision["limit_price_cents"], "decision.limit_price_cents", nullable=True, minimum=1, maximum=99)
    if decision["order_placed"] and not decision["order_attempted"]:
        raise ValidationError("order_placed requires order_attempted")
    if decision["order_attempted"] and not decision["order_intent_created"]:
        raise ValidationError("order_attempted requires order_intent_created")

    outcome = _mapping(root["outcome"], "outcome")
    _exact_keys(outcome, OUTCOME_KEYS, "outcome")
    if outcome["fill_status"] not in {"not_traded", "unfilled", "partially_filled", "filled"}:
        raise ValidationError("outcome.fill_status is unsupported")
    _integer(outcome["filled_quantity"], "outcome.filled_quantity", nullable=True, minimum=0)
    _number(outcome["average_fill_price"], "outcome.average_fill_price", nullable=True, minimum=0, maximum=100)
    if outcome["settlement_result"] not in {"yes", "no", "pending", None}:
        raise ValidationError("outcome.settlement_result is unsupported")
    if outcome["resolved_at"] is not None:
        parse_utc_timestamp(outcome["resolved_at"], field="outcome.resolved_at")
    for field in ("realized_pnl", "hypothetical_pnl_if_traded"):
        _number(outcome[field], f"outcome.{field}", nullable=True)
    _number(outcome["estimated_fill_feasibility"], "outcome.estimated_fill_feasibility", nullable=True, minimum=0, maximum=1)
    _number(outcome["brier_score"], "outcome.brier_score", nullable=True, minimum=0, maximum=1)
    _number(outcome["log_loss"], "outcome.log_loss", nullable=True, minimum=0)
    _string(outcome["calibration_bucket"], "outcome.calibration_bucket", nullable=True, max_length=16)
    if outcome["fill_status"] in {"partially_filled", "filled"}:
        if not outcome["filled_quantity"] or outcome["average_fill_price"] is None:
            raise ValidationError("filled outcomes require positive quantity and average_fill_price")

    research = _mapping(root["research"], "research")
    _exact_keys(research, RESEARCH_KEYS, "research")
    _string(research["experiment_id"], "research.experiment_id", nullable=True, max_length=128)
    if research["train_or_holdout"] not in {"train", "validation", "holdout", "not_applicable"}:
        raise ValidationError("research.train_or_holdout is unsupported")
    _string(research["replay_policy_version"], "research.replay_policy_version", nullable=True, max_length=128)
    _number(research["review_score"], "research.review_score", nullable=True)
    if not isinstance(research["review_findings"], list) or len(research["review_findings"]) > 64:
        raise ValidationError("research.review_findings must be a bounded list")
    for finding in research["review_findings"]:
        _string(finding, "research.review_findings[]", max_length=512)
    _bool(research["accepted_into_knowledge_base"], "research.accepted_into_knowledge_base")
    if research["provenance"] not in {"live_discovery", "offline_replay", "synthetic_fixture", "inner_loop_hypothesis"}:
        raise ValidationError("research.provenance is unsupported")
    parse_utc_timestamp(research["immutable_created_at"], field="research.immutable_created_at")
    if research["accepted_into_knowledge_base"] and outcome["settlement_result"] not in {"yes", "no"}:
        raise ValidationError("knowledge-base acceptance requires resolved settlement ground truth")

    expected_id = deterministic_candidate_id(root)
    if identity["candidate_id"] != expected_id:
        raise ValidationError(f"candidate_id does not match deterministic identity; expected {expected_id}")


def _event_exact(payload: Mapping[str, Any], keys: set[str], event_type: str) -> None:
    _exact_keys(payload, keys, f"event.{event_type}")


def validate_event_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    """Validate one bounded event payload; unknown event types fail closed."""

    if event_type not in EVENT_TYPES:
        raise ValidationError(f"unsupported event_type: {event_type!r}")
    body = _mapping(payload, f"event.{event_type}")
    reject_secret_material(body, path=f"event.{event_type}")

    if event_type == "candidate_observed":
        validate_candidate_payload(body)
        outcome = body["outcome"]
        unresolved = (
            outcome["fill_status"] == "not_traded"
            and outcome["filled_quantity"] is None
            and outcome["average_fill_price"] is None
            and outcome["settlement_result"] in {None, "pending"}
            and outcome["resolved_at"] is None
            and outcome["realized_pnl"] is None
            and outcome["hypothetical_pnl_if_traded"] is None
            and outcome["brier_score"] is None
            and outcome["log_loss"] is None
            and outcome["calibration_bucket"] is None
        )
        if not unresolved or body["research"]["accepted_into_knowledge_base"]:
            raise LeakageError("candidate_observed must not contain later fill, settlement, score, or acceptance outcomes")
        return
    if event_type == "gate_evaluated":
        _event_exact(body, {"gates"}, event_type)
        gates = _mapping(body["gates"], "event.gate_evaluated.gates")
        _exact_keys(gates, GATE_KEYS, "event.gate_evaluated.gates")
        for field in GATE_KEYS - {"gate_failure_kinds"}:
            _bool(gates[field], f"event.gate_evaluated.gates.{field}")
        return
    if event_type == "order_intent_created":
        _event_exact(body, {"internal_order_reference", "quantity", "limit_price_cents"}, event_type)
        _string(body["internal_order_reference"], "event.order_intent_created.internal_order_reference", max_length=128)
        _integer(body["quantity"], "event.order_intent_created.quantity", minimum=1)
        _integer(body["limit_price_cents"], "event.order_intent_created.limit_price_cents", minimum=1, maximum=99)
        return
    if event_type == "order_attempted":
        _event_exact(body, {"internal_order_reference"}, event_type)
        _string(body["internal_order_reference"], "event.order_attempted.internal_order_reference", max_length=128)
        return
    if event_type == "order_result":
        _event_exact(body, {"internal_order_reference", "placed", "rejection_reason"}, event_type)
        _string(body["internal_order_reference"], "event.order_result.internal_order_reference", max_length=128)
        _bool(body["placed"], "event.order_result.placed")
        _string(body["rejection_reason"], "event.order_result.rejection_reason", nullable=True, max_length=256)
        return
    if event_type == "fill_observed":
        _event_exact(body, {"fill_status", "filled_quantity", "average_fill_price", "fees"}, event_type)
        if body["fill_status"] not in {"unfilled", "partially_filled", "filled"}:
            raise ValidationError("event.fill_observed.fill_status is unsupported")
        _integer(body["filled_quantity"], "event.fill_observed.filled_quantity", minimum=0)
        _number(body["average_fill_price"], "event.fill_observed.average_fill_price", nullable=True, minimum=0, maximum=100)
        _number(body["fees"], "event.fill_observed.fees", nullable=True, minimum=0)
        if body["fill_status"] in {"partially_filled", "filled"} and (body["filled_quantity"] <= 0 or body["average_fill_price"] is None):
            raise ValidationError("filled event requires positive quantity and average_fill_price")
        return
    if event_type == "settlement_observed":
        _event_exact(body, {"settlement_result", "resolved_at"}, event_type)
        if body["settlement_result"] not in {"yes", "no"}:
            raise ValidationError("event.settlement_observed.settlement_result must be yes or no")
        parse_utc_timestamp(body["resolved_at"], field="event.settlement_observed.resolved_at")
        return
    if event_type == "hypothetical_outcome_computed":
        _event_exact(body, {"hypothetical_pnl_if_traded", "estimated_fill_feasibility", "fees"}, event_type)
        _number(body["hypothetical_pnl_if_traded"], "event.hypothetical_outcome_computed.hypothetical_pnl_if_traded", nullable=True)
        _number(body["estimated_fill_feasibility"], "event.hypothetical_outcome_computed.estimated_fill_feasibility", minimum=0, maximum=1)
        _number(body["fees"], "event.hypothetical_outcome_computed.fees", nullable=True, minimum=0)
        return
    if event_type == "experiment_assignment":
        keys = {"experiment_id", "split", "strategy_version", "assigned_at", "train_end", "validation_end", "holdout_end"}
        _event_exact(body, keys, event_type)
        _string(body["experiment_id"], "event.experiment_assignment.experiment_id", max_length=128)
        if body["split"] not in {"train", "validation", "holdout"}:
            raise ValidationError("event.experiment_assignment.split is unsupported")
        _string(body["strategy_version"], "event.experiment_assignment.strategy_version", max_length=128)
        for field in ("assigned_at", "train_end", "validation_end", "holdout_end"):
            parse_utc_timestamp(body[field], field=f"event.experiment_assignment.{field}")
        return
    if event_type == "review_recorded":
        _event_exact(body, {"review_score", "review_findings", "accepted_into_knowledge_base", "provenance"}, event_type)
        _number(body["review_score"], "event.review_recorded.review_score", nullable=True)
        if not isinstance(body["review_findings"], list) or len(body["review_findings"]) > 64:
            raise ValidationError("event.review_recorded.review_findings must be a bounded list")
        for finding in body["review_findings"]:
            _string(finding, "event.review_recorded.review_findings[]", max_length=512)
        _bool(body["accepted_into_knowledge_base"], "event.review_recorded.accepted_into_knowledge_base")
        if body["provenance"] not in {"offline_replay", "inner_loop_hypothesis", "outer_loop_settlement"}:
            raise ValidationError("event.review_recorded.provenance is unsupported")
        return
    raise AssertionError(f"validator missing for supported event type {event_type}")
