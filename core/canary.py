#!/usr/bin/env python3
"""
Pre-run Canary Check — Reliability Phase 1, Module 4

Runs at the very start of every cron invocation, takes <=30s wall-clock,
and validates the full stack end-to-end against a known fixture market.

If any critical check fails, the cron run aborts BEFORE burning budget
on a degraded run.

CRITICAL: The canary does NOT place real orders. It goes through every
step of the pipeline EXCEPT the final order submission step.
"""

from __future__ import annotations

import logging
import math
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Project imports
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

import config as global_config
from core.stage_budget import StageBudget

logger = logging.getLogger(__name__)

# ─── Canary Result Dataclasses ────────────────────────────────────────────────


@dataclass
class CanaryResult:
    """Result from a single canary check."""
    passed: bool
    check_name: str
    elapsed_seconds: float
    error: Optional[str] = None
    details: dict = field(default_factory=dict)


class CanaryFailure(Exception):
    """Raised when a critical canary check fails and the run should abort."""
    def __init__(self, check_name: str, message: str, results: list[CanaryResult]):
        self.check_name = check_name
        self.message = message
        self.results = results
        super().__init__(f"[canary] CRITICAL FAILURE in '{check_name}': {message}")


# ─── Scratchpad helpers ───────────────────────────────────────────────────────

def _scratchpad_path() -> Path:
    base = Path("/opt/slimy/pm_updown_bot_bundle")
    return base / "logs" / "scratchpad"


def _write_scratchpad_event(event_type: str, cron_run_id: str, total_elapsed: float,
                             per_check_results: list, failed_check: str = None,
                             error_message: str = None):
    """Write a canary_pass or canary_failure event to scratchpad."""
    entry = {
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cron_run_id": cron_run_id,
        "total_elapsed_seconds": round(total_elapsed, 3),
        "per_check_results": [
            {
                "check_name": r.check_name,
                "passed": r.passed,
                "elapsed_seconds": round(r.elapsed_seconds, 3),
                "error": r.error,
                "details": r.details,
            }
            for r in per_check_results
        ],
    }
    if failed_check:
        entry["failed_check_name"] = failed_check
    if error_message:
        entry["error_message"] = error_message

    try:
        path = _scratchpad_path() / f"{event_type}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(__import__("json").dumps(entry) + "\n")
    except Exception as e:
        logger.warning("[canary] scratchpad write failed: %s", e)


# ─── Check 1: Provider Health ─────────────────────────────────────────────────

def _check_provider_health(budget: StageBudget) -> CanaryResult:
    """
    Check that at least one AI provider returns a valid probability.

    Budget: 15s total, 5s per provider.
    Pass: >=1 provider returns a float in [0.0, 1.0]
    Fail: ALL providers fail (cascade has no fallback if all fail).
    Soft warn: individual provider fails but cascade has survivors (log only).
    """
    check_name = "provider_health"
    tick = time.monotonic()

    # Import here so the module is still loadable even if sent scorer's imports change
    try:
        from strategies.sentiment_scorer import get_ai_prior
    except Exception as exc:
        return CanaryResult(
            passed=False,
            check_name=check_name,
            elapsed_seconds=time.monotonic() - tick,
            error=f"Could not import get_ai_prior: {exc}",
        )

    question = global_config.CANARY_QUESTION
    per_provider_results = []

    for provider_name in global_config.SENTIMENT_PROVIDERS:
        if budget.exhausted():
            per_provider_results.append({
                "provider": provider_name,
                "status": "skipped_budget_exhausted",
            })
            continue

        remaining = budget.remaining()
        if remaining <= 0:
            break

        per_tick = time.monotonic()
        try:
            # tier="premium" uses grok_420 + glm fallback cascade
            prob = get_ai_prior(
                market_text=question,
                tier="premium",
                market_ticker="CANARY",
                timeout=min(5.0, remaining),
            )
            elapsed_provider = time.monotonic() - per_tick

            if prob is None:
                per_provider_results.append({
                    "provider": provider_name,
                    "status": "returned_none",
                    "elapsed": round(elapsed_provider, 3),
                })
            elif not (0.0 <= prob <= 1.0):
                per_provider_results.append({
                    "provider": provider_name,
                    "status": "out_of_range",
                    "prob": prob,
                    "elapsed": round(elapsed_provider, 3),
                })
            else:
                per_provider_results.append({
                    "provider": provider_name,
                    "status": "ok",
                    "prob": round(prob, 4),
                    "elapsed": round(elapsed_provider, 3),
                })
                # Found a working provider — cascade succeeds
                elapsed = time.monotonic() - tick
                return CanaryResult(
                    passed=True,
                    check_name=check_name,
                    elapsed_seconds=elapsed,
                    details={
                        "providers_tested": len(per_provider_results),
                        "per_provider": per_provider_results,
                        "winner": provider_name,
                    },
                )
        except Exception as exc:
            per_provider_results.append({
                "provider": provider_name,
                "status": "exception",
                "error": str(exc),
                "elapsed": round(time.monotonic() - per_tick, 3),
            })

    elapsed = time.monotonic() - tick

    # All providers failed
    return CanaryResult(
        passed=False,
        check_name=check_name,
        elapsed_seconds=elapsed,
        error=f"All {len(per_provider_results)} providers failed",
        details={"per_provider": per_provider_results},
    )


# ─── Check 2: Kalshi Auth + Fetch ─────────────────────────────────────────────

def _check_kalshi_auth_and_fetch(budget: StageBudget) -> CanaryResult:
    """
    Verify Kalshi API auth is valid and a known stable market can be fetched.

    Budget: 5s total.
    Pass: valid JSON response, expected fields present, no auth error, <5s.
    Fail: auth error, missing fields, or timeout.
    """
    check_name = "kalshi_auth_and_fetch"
    tick = time.monotonic()

    ticker = global_config.CANARY_TICKER
    remaining = budget.remaining()
    timeout = min(5.0, remaining)

    try:
        import os
        import requests
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes
        import base64
        import time as time_mod

        api_key = os.getenv("KALSHI_KEY")
        secret_file = os.getenv("KALSHI_SECRET_FILE", "./kalshi_private_key.pem")

        if not api_key:
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=time.monotonic() - tick,
                error="KALSHI_KEY environment variable not set",
            )

        # Load private key
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)

        # Build signed request for single market
        path = f"/trade-api/v2/markets/{ticker}"
        timestamp = str(int(time_mod.time() * 1000))
        msg = f"{timestamp}GET{path}"
        signature = private_key.sign(
            msg.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode()

        headers = {
            "KALSHI-ACCESS-KEY": api_key,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

        base_url = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
        url = f"{base_url}{path}"

        resp = requests.get(url, headers=headers, timeout=timeout)
        elapsed = time.monotonic() - tick

        # Check auth failure
        if resp.status_code in (401, 403):
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=elapsed,
                error=f"Auth failed: HTTP {resp.status_code}",
                details={"status_code": resp.status_code},
            )

        # Check other errors
        if resp.status_code != 200:
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=elapsed,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                details={"status_code": resp.status_code},
            )

        # Parse JSON
        try:
            data = resp.json()
        except Exception as exc:
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=elapsed,
                error=f"JSON parse failed: {exc}",
            )

        # Verify expected fields — the API returns yes_bid_dollars/yes_ask_dollars
        # but the market dict may also expose yes_bid/yes_ask for compatibility
        market = data.get("market", {}) if isinstance(data, dict) else {}
        ticker_val = market.get("ticker") or market.get("market_ticker")
        if not ticker_val:
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=elapsed,
                error="Missing ticker in market response",
                details={"available_fields": list(market.keys())[:20]},
            )
        # Check for price fields — API uses yes_bid_dollars/yes_ask_dollars
        # but we also accept yes_bid/yes_ask for compatibility
        price_fields = ["yes_bid_dollars", "yes_ask_dollars", "yes_bid", "yes_ask"]
        has_price = any(f in market and market.get(f) is not None for f in price_fields)
        if not has_price:
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=elapsed,
                error=f"Missing price fields in market response",
                details={"available_fields": list(market.keys())[:20]},
            )

        return CanaryResult(
            passed=True,
            check_name=check_name,
            elapsed_seconds=elapsed,
            details={
                "ticker": market.get("ticker"),
                "yes_bid": market.get("yes_bid"),
                "yes_ask": market.get("yes_ask"),
                "status_code": resp.status_code,
            },
        )

    except Exception as exc:
        elapsed = time.monotonic() - tick
        return CanaryResult(
            passed=False,
            check_name=check_name,
            elapsed_seconds=elapsed,
            error=str(exc),
        )


# ─── Check 3: Pipeline Dry Run ────────────────────────────────────────────────

def _check_pipeline_dry_run(budget: StageBudget, provider_prior: float = None) -> CanaryResult:
    """
    Run the canary ticker through the full pipeline UP TO BUT NOT INCLUDING
    order submission. Confirm all data shapes are valid and sizing returns non-NaN.

    Budget: 10s total.
    Pass: no exceptions, all shapes valid, sizing returns non-NaN float.
    Fail: any exception or NaN/None in computed order.

    Args:
        budget: StageBudget instance with remaining time.
        provider_prior: If provided (from Check 1), use this instead of calling
                        the provider again to avoid duplicate calls.
    """
    check_name = "pipeline_dry_run"
    tick = time.monotonic()

    ticker = global_config.CANARY_TICKER

    try:
        from utils.kalshi import fetch_kalshi_markets
        from strategies.kalshi_optimize import estimate_true_price

        # Step 1: Fetch the canary market
        markets = fetch_kalshi_markets()
        canary_market = None
        for m in markets:
            if m.get("id") == ticker or m.get("ticker") == ticker:
                canary_market = m
                break

        if canary_market is None:
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=time.monotonic() - tick,
                error=f"Canary ticker '{ticker}' not found in {len(markets)} fetched markets",
                details={"fetched_count": len(markets)},
            )

        # Step 2: Get AI prior (use cached result from Check 1 if available)
        question = canary_market.get("question", canary_market.get("title", ""))
        if provider_prior is not None:
            ai_prior = provider_prior
            prior_source = "canary_check_1_cached"
        else:
            if budget.exhausted():
                return CanaryResult(
                    passed=False,
                    check_name=check_name,
                    elapsed_seconds=time.monotonic() - tick,
                    error="Budget exhausted before AI prior call",
                )
            remaining = budget.remaining()
            ai_prior = estimate_true_price(
                market_question=question,
                market_id=ticker,
                tier="premium",
                timeout=min(10.0, remaining),
            )
            prior_source = "pipeline_call"

        if not (0.0 <= ai_prior <= 1.0):
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=time.monotonic() - tick,
                error=f"AI prior out of range: {ai_prior}",
                details={"prior_source": prior_source},
            )

        # Attach AI prior to market (as _ai_true_price)
        canary_market["_ai_true_price"] = ai_prior

        # Step 3: Compute Kelly size (simulate the sizing logic)
        from strategies.kalshi_optimize import calculate_optimal_order_size

        bankroll = 100.0  # use default for canary
        max_pos = 10.0

        order_size = calculate_optimal_order_size(
            bankroll=bankroll,
            num_markets=1,
            risk_cap_usd=max_pos,
        )

        if order_size is None or (isinstance(order_size, float) and math.isnan(order_size)):
            return CanaryResult(
                passed=False,
                check_name=check_name,
                elapsed_seconds=time.monotonic() - tick,
                error="Kelly sizing returned NaN",
                details={"ai_prior": ai_prior, "order_size": order_size},
            )

        # Step 4: Verify proof pack record shape (as if writing)
        from utils.proof import generate_proof

        proof_id = f"canary_dry_run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        proof_data = {
            "canary": True,
            "ticker": ticker,
            "question": question,
            "ai_prior": ai_prior,
            "prior_source": prior_source,
            "order_size": order_size,
            "bankroll": bankroll,
            "max_pos": max_pos,
            "pipeline_stage": "dry_run_complete",
        }

        # generate_proof writes to disk — catch errors but don't fail the canary
        # (proof_pack_write is its own stage with its own budget)
        try:
            generate_proof(proof_id, proof_data)
            proof_written = True
        except Exception as exc:
            logger.warning("[canary] proof pack write in dry-run failed: %s", exc)
            proof_written = False

        elapsed = time.monotonic() - tick

        return CanaryResult(
            passed=True,
            check_name=check_name,
            elapsed_seconds=elapsed,
            details={
                "ticker": ticker,
                "question": question[:80],
                "ai_prior": round(ai_prior, 4),
                "prior_source": prior_source,
                "order_size": round(order_size, 4),
                "proof_written": proof_written,
            },
        )

    except Exception as exc:
        elapsed = time.monotonic() - tick
        import traceback
        return CanaryResult(
            passed=False,
            check_name=check_name,
            elapsed_seconds=elapsed,
            error=f"{type(exc).__name__}: {exc}",
            details={"traceback": traceback.format_exc()[:500]},
        )


# ─── Main Canary Runner ───────────────────────────────────────────────────────

def run_canary() -> tuple[bool, list[CanaryResult]]:
    """
    Execute all canary checks in order.

    Returns:
        (overall_passed: bool, results: list[CanaryResult])
    """
    cron_run_id = str(uuid.uuid4())
    total_budget = global_config.CANARY_BUDGET_SECONDS  # 30s

    logger.info("[canary] Starting canary check (run_id=%s, budget=%.1fs)", cron_run_id, total_budget)

    canary_budget = StageBudget(
        name="canary",
        seconds=float(total_budget),
        cron_run_id=cron_run_id,
    )

    with canary_budget:
        # ── Check 1: Provider Health ───────────────────────────────────────
        check1_budget = StageBudget(
            name="canary_provider_health",
            seconds=15.0,
            start=datetime.now(timezone.utc),
            cron_run_id=cron_run_id,
        )
        with check1_budget:
            result1 = _check_provider_health(check1_budget)

        logger.info(
            "[canary] provider_health: passed=%s elapsed=%.2fs%s",
            result1.passed,
            result1.elapsed_seconds,
            f" error={result1.error}" if result1.error else "",
        )

        # ── Check 2: Kalshi Auth + Fetch ───────────────────────────────────
        # If Check 1 passed and gave us a prior, pass it to Check 3 to avoid
        # a duplicate (potentially expensive) provider call.
        provider_prior_for_check3 = None
        if result1.passed:
            winner_prob = result1.details.get("per_provider", [{}])[0].get("prob")
            # Find the first "ok" result
            for pr in result1.details.get("per_provider", []):
                if pr.get("status") == "ok" and "prob" in pr:
                    provider_prior_for_check3 = pr["prob"]
                    break

        check2_budget = StageBudget(
            name="canary_kalshi_auth",
            seconds=5.0,
            start=datetime.now(timezone.utc),
            cron_run_id=cron_run_id,
        )
        with check2_budget:
            result2 = _check_kalshi_auth_and_fetch(check2_budget)

        logger.info(
            "[canary] kalshi_auth_and_fetch: passed=%s elapsed=%.2fs%s",
            result2.passed,
            result2.elapsed_seconds,
            f" error={result2.error}" if result2.error else "",
        )

        # ── Check 3: Pipeline Dry Run ───────────────────────────────────────
        check3_budget = StageBudget(
            name="canary_pipeline_dry_run",
            seconds=10.0,
            start=datetime.now(timezone.utc),
            cron_run_id=cron_run_id,
        )
        with check3_budget:
            result3 = _check_pipeline_dry_run(check3_budget, provider_prior=provider_prior_for_check3)

        logger.info(
            "[canary] pipeline_dry_run: passed=%s elapsed=%.2fs%s",
            result3.passed,
            result3.elapsed_seconds,
            f" error={result3.error}" if result3.error else "",
        )

    results = [result1, result2, result3]

    total_elapsed = sum(r.elapsed_seconds for r in results)
    all_passed = all(r.passed for r in results)

    # Scratchpad event
    if all_passed:
        _write_scratchpad_event(
            event_type="canary_pass",
            cron_run_id=cron_run_id,
            total_elapsed=total_elapsed,
            per_check_results=results,
        )
    else:
        failed = next((r for r in results if not r.passed), None)
        _write_scratchpad_event(
            event_type="canary_failure",
            cron_run_id=cron_run_id,
            total_elapsed=total_elapsed,
            per_check_results=results,
            failed_check=failed.check_name if failed else "unknown",
            error_message=failed.error if failed else "unknown",
        )

    logger.info(
        "[canary] Complete: passed=%s total_elapsed=%.2fs/%ds",
        all_passed,
        total_elapsed,
        total_budget,
    )

    return all_passed, results


# ─── CLI Entry Point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    print("=" * 60)
    print("CANARY CHECK — Direct Run")
    print("=" * 60)

    passed, results = run_canary()

    print()
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.check_name}: {r.elapsed_seconds:.2f}s")
        if r.error:
            print(f"         Error: {r.error}")

    print()
    if passed:
        print("RESULT: ALL CHECKS PASSED")
        sys.exit(0)
    else:
        print("RESULT: CANARY FAILED")
        sys.exit(1)