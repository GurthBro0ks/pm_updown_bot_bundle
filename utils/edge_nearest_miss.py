"""Redacted nearest-miss diagnostics for main Kalshi edge decisions.

This module only handles public market metadata and derived edge numbers. It
does not read credentials, place orders, or change trading gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from collections import Counter
from typing import Any


DEFAULT_LATEST_PATH = Path("logs/main_edge_nearest_miss_latest.json")
SCHEMA = "main_edge_nearest_miss.v1"
SECRET_MARKER_RE = re.compile(
    r"(secret|token|password|passwd|private[_-]?key|api[_-]?key|authorization|bearer|webhook|signature|credential)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NearestMiss:
    ticker: str
    category: str
    side: str
    price_cents: int
    ai_prior: float
    raw_edge: float
    fee_adjusted_edge: float
    required_threshold: float
    rejection_reason: str
    gate_failure_kind: str = "none"
    gate_failure_kinds: tuple[str, ...] = ()

    def sortable_gap(self) -> float:
        return self.required_threshold - self.fee_adjusted_edge


def latest_summary_path() -> Path:
    return Path(os.getenv("MAIN_EDGE_NEAREST_MISS_PATH", str(DEFAULT_LATEST_PATH)))


def _safe_text(value: Any, *, default: str = "unknown", max_len: int = 96) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if SECRET_MARKER_RE.search(text):
        return "redacted"
    text = re.sub(r"[^A-Za-z0-9_.:+/@-]+", "_", text)
    return text[:max_len] if text else default


def _safe_float(value: Any, *, default: float = 0.0, digits: int = 4) -> float:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def public_category(market: dict[str, Any]) -> str:
    category = (
        market.get("_category")
        or market.get("series_category")
        or market.get("category")
        or "unknown"
    )
    return _safe_text(str(category).lower(), default="unknown", max_len=48)


def make_nearest_miss(
    *,
    market: dict[str, Any],
    side: str,
    price: float,
    ai_prior: float,
    raw_edge: float,
    fee_adjusted_edge: float,
    required_threshold: float,
    rejection_reason: str,
    gate_failure_kinds: list[str] | tuple[str, ...] | None = None,
) -> NearestMiss:
    ticker = market.get("ticker") or market.get("id") or "unknown"
    safe_kinds = tuple(
        _safe_text(kind, default="unknown_gate_failure", max_len=48)
        for kind in (gate_failure_kinds or ())
    )
    primary_kind = safe_kinds[0] if safe_kinds else "none"
    return NearestMiss(
        ticker=_safe_text(ticker),
        category=public_category(market),
        side=_safe_text(side.lower(), default="yes", max_len=12),
        price_cents=_safe_int(float(price) * 100),
        ai_prior=_safe_float(ai_prior),
        raw_edge=_safe_float(raw_edge, digits=2),
        fee_adjusted_edge=_safe_float(fee_adjusted_edge, digits=2),
        required_threshold=_safe_float(required_threshold, digits=2),
        rejection_reason=_safe_text(rejection_reason, max_len=80),
        gate_failure_kind=primary_kind,
        gate_failure_kinds=safe_kinds,
    )


def bounded_nearest_misses(misses: list[NearestMiss], sample_limit: int) -> list[NearestMiss]:
    limit = max(0, int(sample_limit))
    return sorted(misses, key=lambda miss: (miss.sortable_gap(), miss.ticker))[:limit]


def gate_failure_kind_counts(misses: list[NearestMiss]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for miss in misses:
        kinds = miss.gate_failure_kinds or ((miss.gate_failure_kind,) if miss.gate_failure_kind else ())
        for kind in kinds:
            safe_kind = _safe_text(kind, default="unknown_gate_failure", max_len=48)
            if safe_kind != "none":
                counts[safe_kind] += 1
    return dict(sorted(counts.items()))


def gate_failure_kind_counts_string(counts: dict[str, Any] | None) -> str:
    if not counts:
        return "none"
    return ",".join(
        f"{_safe_text(kind, default='unknown_gate_failure', max_len=48)}:{_safe_int(count)}"
        for kind, count in sorted(counts.items())
    )


def public_count_string(counts: dict[str, Any] | None, *, default: str = "none") -> str:
    if not counts:
        return default
    return ",".join(
        f"{_safe_text(kind, default='unknown', max_len=48)}:{_safe_int(count)}"
        for kind, count in sorted(counts.items())
    )


def classify_zero_order_reason(
    *,
    order_placed_count: int,
    order_intent_count: int,
    price_gate_blocked_count: int,
    edge_or_profitability_blocked_count: int,
    ai_processed_count: int,
) -> str:
    if order_placed_count > 0:
        return "orders_placed"
    if ai_processed_count <= 0:
        return "provider_cascade_not_run_or_no_candidates"
    if price_gate_blocked_count > 0 and order_intent_count == price_gate_blocked_count:
        return "price_gate_blocked"
    if edge_or_profitability_blocked_count > 0 and order_intent_count == 0:
        return "edge_or_profitability_blocked"
    if order_intent_count == 0:
        return "no_order_intents_after_ai"
    return "unknown_need_redacted_status_tool"


def build_summary(
    *,
    run_timestamp: str | None,
    ai_processed_count: int,
    order_intent_count: int,
    nearest_misses: list[NearestMiss],
    edge_threshold: float,
    fee_adjusted_edge_threshold: float,
    no_profitable_maker_count: int,
    price_gate_blocked_count: int,
    edge_or_profitability_blocked_count: int,
    order_placed_count: int,
    sample_limit: int = 10,
    order_submission_attempted_count: int = 0,
    order_submission_succeeded_count: int = 0,
    order_submission_failed_count: int = 0,
    submission_skipped_reason_counts: dict[str, int] | None = None,
    post_intent_blocker_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    bounded = bounded_nearest_misses(nearest_misses, sample_limit)
    zero_order_reason = classify_zero_order_reason(
        order_placed_count=order_placed_count,
        order_intent_count=order_intent_count,
        price_gate_blocked_count=price_gate_blocked_count,
        edge_or_profitability_blocked_count=edge_or_profitability_blocked_count,
        ai_processed_count=ai_processed_count,
    )
    return {
        "schema": SCHEMA,
        "run_timestamp": run_timestamp or datetime.now(timezone.utc).isoformat(),
        "ai_processed_count": int(ai_processed_count),
        "order_intent_count": int(order_intent_count),
        "nearest_miss_count": len(nearest_misses),
        "edge_threshold": _safe_float(edge_threshold, digits=2),
        "fee_adjusted_edge_threshold": _safe_float(fee_adjusted_edge_threshold, digits=2),
        "no_profitable_maker_count": int(no_profitable_maker_count),
        "price_gate_blocked_count": int(price_gate_blocked_count),
        "edge_or_profitability_blocked_count": int(edge_or_profitability_blocked_count),
        "order_placed_count": int(order_placed_count),
        "order_submission_attempted_count": int(order_submission_attempted_count),
        "order_submission_succeeded_count": int(order_submission_succeeded_count),
        "order_submission_failed_count": int(order_submission_failed_count),
        "submission_skipped_reason_counts": dict(sorted((submission_skipped_reason_counts or {}).items())),
        "post_intent_blocker_counts": dict(sorted((post_intent_blocker_counts or {}).items())),
        "gate_failure_kind_counts": gate_failure_kind_counts(nearest_misses),
        "zero_order_reason": zero_order_reason,
        "nearest_misses": [miss.__dict__ for miss in bounded],
        "values_printed": "no_secret_values",
    }


def write_summary(summary: dict[str, Any], path: Path | None = None) -> Path:
    target = path or latest_summary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.replace(target)
    return target


def load_summary(path: Path | None = None) -> dict[str, Any] | None:
    target = path or latest_summary_path()
    if not target.is_file():
        return None
    data = json.loads(target.read_text())
    if data.get("schema") != SCHEMA:
        return None
    return data


def sample_string(misses: list[dict[str, Any]]) -> str:
    if not misses:
        return "none"
    parts: list[str] = []
    for miss in misses:
        fields = [
            ("ticker", miss.get("ticker")),
            ("category", miss.get("category")),
            ("side", miss.get("side")),
            ("price_cents", miss.get("price_cents")),
            ("ai_prior", miss.get("ai_prior")),
            ("raw_edge", miss.get("raw_edge")),
            ("fee_adjusted_edge", miss.get("fee_adjusted_edge")),
            ("required_threshold", miss.get("required_threshold")),
            ("rejection_reason", miss.get("rejection_reason")),
            ("gate_failure_kind", miss.get("gate_failure_kind", "none")),
        ]
        parts.append(",".join(f"{key}:{_safe_text(value)}" for key, value in fields))
    return ";".join(parts)


def format_summary(
    summary: dict[str, Any] | None,
    *,
    missing_status: str = "WARN_NO_PRIOR_STRUCTURED_DETAIL",
    artifact: Path | None = None,
) -> str:
    artifact_value = _safe_text(artifact, default="unknown", max_len=160)
    if summary is None:
        fields = [
            ("MAIN_EDGE_NEAREST_MISS", missing_status),
            ("NEAREST_MISS_ARTIFACT", artifact_value),
            ("VALUES_PRINTED", "no_secret_values"),
            ("LATEST_RUN_TIMESTAMP", "unknown"),
            ("AI_PROCESSED_COUNT", "unknown"),
            ("ORDER_INTENT_COUNT", "unknown"),
            ("NEAREST_MISS_COUNT", 0),
            ("EDGE_THRESHOLD", "unknown"),
            ("FEE_ADJUSTED_EDGE_THRESHOLD", "unknown"),
            ("NO_PROFITABLE_MAKER_COUNT", "unknown"),
            ("PRICE_GATE_BLOCKED_COUNT", "unknown"),
            ("EDGE_OR_PROFITABILITY_BLOCKED_COUNT", "unknown"),
            ("GATE_FAILURE_KIND_COUNTS", "none"),
            ("SAMPLE_NEAREST_MISSES_PUBLIC", "none"),
            ("ZERO_ORDER_REASON", "future_cron_run_required_for_structured_detail"),
        ]
    else:
        fields = [
            ("MAIN_EDGE_NEAREST_MISS", "PASS"),
            ("NEAREST_MISS_ARTIFACT", artifact_value),
            ("VALUES_PRINTED", "no_secret_values"),
            ("LATEST_RUN_TIMESTAMP", summary.get("run_timestamp", "unknown")),
            ("AI_PROCESSED_COUNT", summary.get("ai_processed_count", "unknown")),
            ("ORDER_INTENT_COUNT", summary.get("order_intent_count", "unknown")),
            ("NEAREST_MISS_COUNT", summary.get("nearest_miss_count", 0)),
            ("EDGE_THRESHOLD", summary.get("edge_threshold", "unknown")),
            ("FEE_ADJUSTED_EDGE_THRESHOLD", summary.get("fee_adjusted_edge_threshold", "unknown")),
            ("NO_PROFITABLE_MAKER_COUNT", summary.get("no_profitable_maker_count", "unknown")),
            ("PRICE_GATE_BLOCKED_COUNT", summary.get("price_gate_blocked_count", "unknown")),
            ("EDGE_OR_PROFITABILITY_BLOCKED_COUNT", summary.get("edge_or_profitability_blocked_count", "unknown")),
            ("POST_INTENT_BLOCKER_COUNTS", public_count_string(summary.get("post_intent_blocker_counts"), default="unknown")),
            ("ORDER_INTENT_TO_SUBMISSION_STATUS", "intents:{},attempted:{},succeeded:{},failed:{}".format(
                _safe_int(summary.get("order_intent_count")),
                _safe_int(summary.get("order_submission_attempted_count")),
                _safe_int(summary.get("order_submission_succeeded_count")),
                _safe_int(summary.get("order_submission_failed_count")),
            )),
            ("SUBMISSION_SKIPPED_REASON_COUNTS", public_count_string(summary.get("submission_skipped_reason_counts"), default="unknown")),
            ("GATE_FAILURE_KIND_COUNTS", gate_failure_kind_counts_string(summary.get("gate_failure_kind_counts"))),
            ("SAMPLE_NEAREST_MISSES_PUBLIC", sample_string(summary.get("nearest_misses", []))),
            ("ZERO_ORDER_REASON", _safe_text(summary.get("zero_order_reason"), default="unknown")),
        ]
    return "\n".join(f"{key}={value}" for key, value in fields) + "\n"
