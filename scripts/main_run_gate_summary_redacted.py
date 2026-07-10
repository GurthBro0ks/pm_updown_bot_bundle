#!/usr/bin/env python3
"""Redacted read-only gate summary for the latest main run log.

The parser emits counts and coarse classifications only. It does not read
environment files, credential files, proof JSON, or response bodies.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Sequence


DEFAULT_LOG = Path("logs/cron_micro_live.log")
SECRET_MARKER_RE = re.compile(
    r"(secret|token|password|passwd|private[_-]?key|api[_-]?key|authorization|bearer|webhook|signature|credential)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GateSummary:
    status: str
    run_timestamp: str
    exit_code: str
    markets_fetched: int | None
    after_expiry: int | None
    after_category: int | None
    ai_processed: int
    order_intents: int
    price_gate_blocked: int
    edge_or_profitability_blocked: int
    order_submission_processed: int | None
    order_placed: int | None
    post_intent_blocker_counts: str
    order_intent_to_submission_status: str
    submission_skipped_reason_counts: str
    zero_order_reason: str


def _safe_lines(lines: Iterable[str]) -> list[str]:
    return [line.rstrip("\n") for line in lines if not SECRET_MARKER_RE.search(line)]


def _split_runs(lines: Sequence[str]) -> list[list[str]]:
    runs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if "Fetching Kalshi markets" in line or "Starting Phase 1: Kalshi Optimization" in line:
            if current:
                runs.append(current)
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        runs.append(current)
    return runs


def _first_timestamp(lines: Sequence[str]) -> str:
    for line in lines:
        match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if match:
            return match.group(1).replace(" ", "T") + "Z"
        match = re.match(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:[+Z].*)?\]", line)
        if match:
            return match.group(1) + "Z"
    return "unknown"


def _last_int(lines: Sequence[str], pattern: str) -> int | None:
    value = None
    rx = re.compile(pattern)
    for line in lines:
        match = rx.search(line)
        if match:
            value = int(match.group(1))
    return value


def _classify_zero_order_reason(
    *,
    order_placed: int | None,
    markets_fetched: int | None,
    after_expiry: int | None,
    after_category: int | None,
    ai_processed: int,
    order_intents: int,
    price_gate_blocked: int,
    edge_or_profitability_blocked: int,
    order_submission_processed: int | None,
    exit_code: str,
) -> str:
    if order_placed and order_placed > 0:
        return "orders_placed"
    if markets_fetched == 0:
        return "no_markets_fetched"
    if after_expiry == 0:
        return "no_current_3_day_markets"
    if after_category == 0:
        return "no_allowed_category_markets"
    if ai_processed == 0:
        return "provider_cascade_not_run_or_no_candidates"
    if price_gate_blocked > 0 and order_intents == price_gate_blocked:
        return "price_gate_blocked"
    if edge_or_profitability_blocked > 0 and order_intents == 0:
        return "edge_or_profitability_blocked"
    if order_submission_processed == 0 and order_intents == 0:
        return "no_order_intents_after_ai"
    if exit_code not in {"0", "unknown"}:
        return "order_submission_failed_or_run_failed"
    return "unknown_need_redacted_status_tool"


def summarize_lines(lines: Sequence[str]) -> GateSummary:
    safe = _safe_lines(lines)
    runs = _split_runs(safe)
    if not runs:
        return GateSummary(
            status="WARN_NO_MAIN_RUN",
            run_timestamp="unknown",
            exit_code="unknown",
            markets_fetched=None,
            after_expiry=None,
            after_category=None,
            ai_processed=0,
            order_intents=0,
            price_gate_blocked=0,
            edge_or_profitability_blocked=0,
            order_submission_processed=None,
            order_placed=None,
            post_intent_blocker_counts="unknown",
            order_intent_to_submission_status="unknown",
            submission_skipped_reason_counts="unknown",
            zero_order_reason="no_main_run_found",
        )

    run = runs[-1]
    markets_fetched = _last_int(run, r"Fetched (\d+) markets")
    expiry_match = None
    category_match = None
    exit_code = "unknown"
    order_submission_processed = None
    post_intent_blocker_counts = "unknown"
    order_intent_to_submission_status = "unknown"
    submission_skipped_reason_counts = "unknown"

    for line in run:
        match = re.search(r"\[EXPIRY\] Filtered \d+ -> (\d+) markets", line)
        if match:
            expiry_match = int(match.group(1))
        match = re.search(r"\[CATEGORY\] Filtered \d+ -> (\d+) markets", line)
        if match:
            category_match = int(match.group(1))
        match = re.search(r"order_submission: elapsed=.* processed=(\d+)", line)
        if match:
            order_submission_processed = int(match.group(1))
        match = re.search(r"Exit code: (\d+)", line)
        if match:
            exit_code = match.group(1)
        match = re.search(r"\[ORDER_DIAG\] POST_INTENT_BLOCKER_COUNTS=([^\s]+)", line)
        if match:
            post_intent_blocker_counts = match.group(1)
        match = re.search(r"\[ORDER_DIAG\] ORDER_INTENT_TO_SUBMISSION_STATUS=([^\s]+)", line)
        if match:
            order_intent_to_submission_status = match.group(1)
        match = re.search(r"\[ORDER_DIAG\] SUBMISSION_SKIPPED_REASON_COUNTS=([^\s]+)", line)
        if match:
            submission_skipped_reason_counts = match.group(1)

    ai_processed = sum(1 for line in run if "[kelly] AI prior:" in line)
    order_intents = sum(1 for line in run if re.search(r"Market [A-Za-z0-9_.:-]+: (YES|NO) order", line))
    price_gate_blocked = sum(1 for line in run if "[PRICE] Skipping" in line)
    edge_or_profitability_blocked = sum(
        1
        for line in run
        if "[EDGE] Rejecting" in line
        or "No profitable maker markets found" in line
    )
    order_placed = _last_int(run, r"Total orders placed: (\d+)")
    zero_order_reason = _classify_zero_order_reason(
        order_placed=order_placed,
        markets_fetched=markets_fetched,
        after_expiry=expiry_match,
        after_category=category_match,
        ai_processed=ai_processed,
        order_intents=order_intents,
        price_gate_blocked=price_gate_blocked,
        edge_or_profitability_blocked=edge_or_profitability_blocked,
        order_submission_processed=order_submission_processed,
        exit_code=exit_code,
    )

    return GateSummary(
        status="PASS",
        run_timestamp=_first_timestamp(run),
        exit_code=exit_code,
        markets_fetched=markets_fetched,
        after_expiry=expiry_match,
        after_category=category_match,
        ai_processed=ai_processed,
        order_intents=order_intents,
        price_gate_blocked=price_gate_blocked,
        edge_or_profitability_blocked=edge_or_profitability_blocked,
        order_submission_processed=order_submission_processed,
        order_placed=order_placed,
        post_intent_blocker_counts=post_intent_blocker_counts,
        order_intent_to_submission_status=order_intent_to_submission_status,
        submission_skipped_reason_counts=submission_skipped_reason_counts,
        zero_order_reason=zero_order_reason,
    )


def format_summary(summary: GateSummary, *, artifact: Path | None = None) -> str:
    fields = [
        ("MAIN_RUN_GATE_SUMMARY", summary.status),
        ("LATEST_RUN_ARTIFACT", artifact or "unknown"),
        ("RUN_TIMESTAMP", summary.run_timestamp),
        ("EXIT_CODE", summary.exit_code),
        ("MARKETS_FETCHED", summary.markets_fetched),
        ("AFTER_EXPIRY", summary.after_expiry),
        ("AFTER_CATEGORY", summary.after_category),
        ("AI_PROCESSED", summary.ai_processed),
        ("ORDER_INTENTS", summary.order_intents),
        ("PRICE_GATE_BLOCKED", summary.price_gate_blocked),
        ("EDGE_OR_PROFITABILITY_BLOCKED", summary.edge_or_profitability_blocked),
        ("ORDER_SUBMISSION_PROCESSED", summary.order_submission_processed),
        ("ORDER_PLACED", summary.order_placed),
        ("POST_INTENT_BLOCKER_COUNTS", summary.post_intent_blocker_counts),
        ("ORDER_INTENT_TO_SUBMISSION_STATUS", summary.order_intent_to_submission_status),
        ("SUBMISSION_SKIPPED_REASON_COUNTS", summary.submission_skipped_reason_counts),
        ("ZERO_ORDER_REASON", summary.zero_order_reason),
        ("VALUES_PRINTED", "no_secret_values"),
    ]
    return "\n".join(f"{key}={value if value is not None else 'unknown'}" for key, value in fields) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print redacted latest main-run gate counts.")
    parser.add_argument("--log", default=str(DEFAULT_LOG), help="Cron/main log path to parse.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.log)
    if not path.is_file():
        print(format_summary(summarize_lines([])), end="")
        return 1
    summary = summarize_lines(path.read_text(errors="replace").splitlines())
    print(format_summary(summary, artifact=path), end="")
    return 0 if summary.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
