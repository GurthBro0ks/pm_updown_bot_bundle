"""Market-pipeline adapter for observational candidate capture only."""

from __future__ import annotations

import re
import subprocess
from typing import Any, Mapping

from .capture import CaptureConfig, CaptureStatus, ShadowCaptureBuffer
from .models import SCHEMA_VERSION, deterministic_candidate_id, utc_now


STRATEGY_VERSION = "kalshi-optimize@phase1b"
POLICY_VERSION = "kalshi-main-policy-v1"
_GIT_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def _resolve_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd="/opt/slimy/pm_updown_bot_bundle",
            check=True,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        commit = result.stdout.strip()
        if _GIT_COMMIT.fullmatch(commit):
            return commit
    except Exception:
        pass
    return "0000000"


def _environment_mode(mode: str) -> str:
    return {
        "shadow": "dry_run",
        "micro-live": "micro_live",
        "real-live": "live",
    }.get(mode, "dry_run")


def _price_cents(value: Any, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    cents = int(round(float(value) * 100))
    if nullable and cents == 0:
        return None
    return cents


def _gate_results(failure_kinds: list[str]) -> dict[str, Any]:
    gates: dict[str, Any] = {
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
        "gate_failure_kinds": list(dict.fromkeys(failure_kinds)),
    }
    kind_to_gate = {
        "expiry": "expiry_pass",
        "category": "category_pass",
        "weather": "weather_exclusion_pass",
        "price_below_minimum": "price_pass",
        "edge_below_threshold": "edge_pass",
        "profitability": "profitability_pass",
        "fallback_prior": "fallback_prior_pass",
        "liquidity_min": "liquidity_pass",
        "market_end_time": "market_end_time_pass",
        "price_sanity": "price_sanity_pass",
        "prior_validation": "price_sanity_pass",
        "size_limit": "size_pass",
        "bankroll": "bankroll_pass",
        "notional": "notional_pass",
        "duplicate": "duplicate_pass",
        "open_order": "open_order_pass",
    }
    for kind in failure_kinds:
        gate = kind_to_gate.get(kind)
        if gate:
            gates[gate] = False
    if "edge_below_threshold" in failure_kinds:
        gates["profitability_pass"] = False
    return gates


class CandidateCaptureRuntime:
    """Build Phase 1A payloads from finalized public strategy observations."""

    def __init__(
        self,
        *,
        mode: str,
        run_id: str,
        run_timestamp: str | None = None,
        git_commit: str | None = None,
        config: CaptureConfig | None = None,
    ):
        resolved_config = config or CaptureConfig.from_environment()
        self.mode = mode
        self.run_id = run_id
        self.run_timestamp = run_timestamp or utc_now()
        self.git_commit = git_commit or (_resolve_git_commit() if resolved_config.ready else "0000000")
        self.buffer = ShadowCaptureBuffer(resolved_config)

    @property
    def status(self) -> CaptureStatus:
        return self.buffer.status

    def record_evaluation(
        self,
        *,
        market: Mapping[str, Any],
        observed_price: float,
        ai_prior: float,
        fallback_prior_used: bool,
        raw_edge: float,
        fee_adjusted_edge: float | None,
        required_threshold: float,
        rejection_reason: str | None,
        gate_failure_kinds: list[str] | tuple[str, ...],
        order_intent_created: bool,
        intent_price: float | None = None,
        maker_assumption: str = "unknown",
        expected_value: float | None = None,
    ) -> bool:
        if not self.buffer.config.enabled:
            return False
        try:
            ticker = str(market.get("ticker") or market.get("id") or "")
            series = str(market.get("series_ticker") or ticker.split("-")[0] or "unknown")
            category = str(market.get("_category") or market.get("series_category") or market.get("category") or "unknown").lower()
            public_title = str(market.get("title") or market.get("question") or ticker)
            expiry = market.get("close_time") or market.get("expiration_date")
            observed_cents = _price_cents(observed_price)
            bid_cents = _price_cents(market.get("_yes_bid_price"), nullable=True)
            ask_cents = _price_cents(market.get("_yes_ask_price"), nullable=True)
            spread_cents = None
            if bid_cents is not None and ask_cents is not None:
                spread_cents = max(0, ask_cents - bid_cents)
            failure_kinds = list(dict.fromkeys(str(item) for item in gate_failure_kinds))
            gates = _gate_results(failure_kinds)
            estimated_fees = None
            if fee_adjusted_edge is not None:
                estimated_fees = max(0.0, float(raw_edge) - float(fee_adjusted_edge))
            edge = {
                "raw_edge": float(raw_edge),
                "fee_adjusted_edge": None if fee_adjusted_edge is None else float(fee_adjusted_edge),
                "required_threshold": float(required_threshold),
                "estimated_fees": estimated_fees,
                "maker_assumption": maker_assumption,
                "expected_value": expected_value,
            }
            decision = {
                "rejection_reason": rejection_reason,
                "order_intent_created": bool(order_intent_created),
                "order_attempted": False,
                "order_placed": False,
                "internal_order_reference": None,
                "quantity": None,
                "limit_price_cents": None,
            }
            candidate: dict[str, Any] = {
                "identity": {
                    "schema_version": SCHEMA_VERSION,
                    "candidate_id": "pending",
                    "run_id": self.run_id,
                    "run_timestamp": self.run_timestamp,
                    "strategy_version": STRATEGY_VERSION,
                    "git_commit": self.git_commit,
                    "environment_mode": _environment_mode(self.mode),
                },
                "market": {
                    "ticker": ticker,
                    "series": series,
                    "category": category,
                    "public_title": public_title,
                    "expiry": expiry,
                    "settlement_rule_reference": "venue_public_rules",
                    "side": "yes",
                    "observed_price_cents": observed_cents,
                    "bid_cents": bid_cents,
                    "ask_cents": ask_cents,
                    "spread_cents": spread_cents,
                },
                "prediction": {
                    "provider": "sentiment_cascade",
                    "model": str(market.get("_ai_tier") or "unknown"),
                    "policy_version": POLICY_VERSION,
                    "ai_prior": float(ai_prior),
                    "fallback_prior_used": bool(fallback_prior_used),
                    "confidence": {
                        "confidence_bucket": "unknown",
                        "self_reported_confidence": None,
                    },
                    "source_data_timestamps": {"market_snapshot": self.run_timestamp},
                },
                "edge": edge,
                "gates": gates,
                "decision": decision,
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
                    "provenance": "live_discovery",
                    "immutable_created_at": self.run_timestamp,
                },
            }
            candidate["identity"]["candidate_id"] = deterministic_candidate_id(candidate)
            candidate_id = candidate["identity"]["candidate_id"]
            order_intent = None
            if order_intent_created:
                reference = f"intent_{candidate_id[5:21]}"
                intended_cents = _price_cents(intent_price if intent_price is not None else observed_price)
                limit_price_cents = max(1, min(99, int(intended_cents)))
                candidate["decision"].update(
                    {
                        "internal_order_reference": reference,
                        "quantity": 1,
                        "limit_price_cents": limit_price_cents,
                    }
                )
                order_intent = {
                    "internal_order_reference": reference,
                    "quantity": 1,
                    "limit_price_cents": limit_price_cents,
                }
            gate_payload = {"gates": gates, "edge": edge, "decision": candidate["decision"]}
            return self.buffer.add_candidate(
                candidate_payload=candidate,
                event_timestamp=self.run_timestamp,
                gate_payload=gate_payload,
                order_intent_payload=order_intent,
            )
        except Exception:
            self.buffer.drop("malformed_payload")
            return False

    def flush(self) -> CaptureStatus:
        return self.buffer.flush()


def create_candidate_capture(
    *,
    mode: str,
    run_id: str,
    run_timestamp: str | None = None,
    git_commit: str | None = None,
) -> CandidateCaptureRuntime:
    return CandidateCaptureRuntime(
        mode=mode,
        run_id=run_id,
        run_timestamp=run_timestamp,
        git_commit=git_commit,
    )
