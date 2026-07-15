#!/usr/bin/env python3
"""Evaluate resolved ledger candidates deterministically and read-only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.candidate_ledger.replay import realized_pnl, strategy_version_comparison
from research.candidate_ledger.store import CandidateLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic read-only binary replay over a local offline/research ledger. No network, production capture, or trading.",
    )
    parser.add_argument("--database", required=True, help="Explicit local SQLite database file path (no default).")
    return parser


def evaluate(ledger: CandidateLedger) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    unknown_fee_count = 0
    unresolved_count = 0
    for candidate in ledger.candidate_payloads():
        candidate_id = candidate["identity"]["candidate_id"]
        history = ledger.history(candidate_id)
        settlement = next((event.payload for event in reversed(history) if event.event_type == "settlement_observed"), None)
        fill = next((event.payload for event in reversed(history) if event.event_type == "fill_observed"), None)
        if settlement is None:
            unresolved_count += 1
            continue
        pnl = None
        if fill and fill["fill_status"] in {"partially_filled", "filled"}:
            pnl = realized_pnl(
                side=candidate["market"]["side"],
                settlement_result=settlement["settlement_result"],
                average_fill_price_cents=fill["average_fill_price"],
                filled_quantity=fill["filled_quantity"],
                fees=fill["fees"],
            )
            if fill["fees"] is None:
                unknown_fee_count += 1
        observations.append(
            {
                "strategy_version": candidate["identity"]["strategy_version"],
                "run_timestamp": candidate["identity"]["run_timestamp"],
                "candidate_id": candidate_id,
                "probability_yes": candidate["prediction"]["ai_prior"],
                "settlement_result": settlement["settlement_result"],
                "pnl": pnl,
            }
        )
    return {
        "status": "PASS",
        "read_only": True,
        "raw_candidates_included": False,
        "resolved_candidate_count": len(observations),
        "unresolved_candidate_count": unresolved_count,
        "unknown_fee_count": unknown_fee_count,
        "strategy_versions": strategy_version_comparison(observations),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with CandidateLedger.open_read_only(args.database) as ledger:
        result = evaluate(ledger)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
