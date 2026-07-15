from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from research.candidate_ledger.models import deterministic_candidate_id


def _candidate_template() -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "identity": {
            "schema_version": "1.0.0",
            "candidate_id": "pending",
            "run_id": "synthetic-run-001",
            "run_timestamp": "2026-01-10T12:00:00Z",
            "strategy_version": "synthetic-strategy@v1",
            "git_commit": "005a8ee76cfe5d86a76fbe6053a61dd7edb41b1e",
            "environment_mode": "replay_offline",
        },
        "market": {
            "ticker": "SYNTHETIC-EVENT-YES",
            "series": "SYNTHETIC-EVENT",
            "category": "economics",
            "public_title": "Synthetic candidate fixture",
            "expiry": "2026-01-12T12:00:00Z",
            "settlement_rule_reference": "synthetic-rule-v1",
            "side": "yes",
            "observed_price_cents": 60,
            "bid_cents": 59,
            "ask_cents": 61,
            "spread_cents": 2,
        },
        "prediction": {
            "provider": "synthetic",
            "model": "fixture-model-v1",
            "policy_version": "fixture-policy-v1",
            "ai_prior": 0.7,
            "fallback_prior_used": False,
            "confidence": {"confidence_bucket": "medium", "self_reported_confidence": 0.6},
            "source_data_timestamps": {"market_snapshot": "2026-01-10T11:59:00Z"},
        },
        "edge": {
            "raw_edge": 0.1,
            "fee_adjusted_edge": 0.08,
            "required_threshold": 0.05,
            "estimated_fees": 0.02,
            "maker_assumption": "taker",
            "expected_value": 0.08,
        },
        "gates": {
            "expiry_pass": True,
            "category_pass": True,
            "weather_exclusion_pass": True,
            "price_pass": True,
            "edge_pass": True,
            "profitability_pass": True,
            "fallback_prior_pass": True,
            "liquidity_pass": True,
            "market_end_time_pass": True,
            "price_sanity_pass": True,
            "size_pass": True,
            "bankroll_pass": True,
            "notional_pass": True,
            "duplicate_pass": True,
            "open_order_pass": True,
            "gate_failure_kinds": [],
        },
        "decision": {
            "rejection_reason": None,
            "order_intent_created": True,
            "order_attempted": True,
            "order_placed": True,
            "internal_order_reference": "synthetic-order-001",
            "quantity": 2,
            "limit_price_cents": 60,
        },
        "outcome": {
            "fill_status": "not_traded",
            "filled_quantity": None,
            "average_fill_price": None,
            "settlement_result": "pending",
            "resolved_at": None,
            "realized_pnl": None,
            "hypothetical_pnl_if_traded": None,
            "estimated_fill_feasibility": None,
            "brier_score": None,
            "log_loss": None,
            "calibration_bucket": None,
        },
        "research": {
            "experiment_id": None,
            "train_or_holdout": "not_applicable",
            "replay_policy_version": None,
            "review_score": None,
            "review_findings": [],
            "accepted_into_knowledge_base": False,
            "provenance": "synthetic_fixture",
            "immutable_created_at": "2026-01-10T12:00:01Z",
        },
    }
    candidate["identity"]["candidate_id"] = deterministic_candidate_id(candidate)
    return candidate


@pytest.fixture
def candidate_factory():
    def factory(**overrides: Any) -> dict[str, Any]:
        candidate = deepcopy(_candidate_template())
        for dotted_path, value in overrides.items():
            target = candidate
            parts = dotted_path.split("__")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
        candidate["identity"]["candidate_id"] = deterministic_candidate_id(candidate)
        return candidate

    return factory
