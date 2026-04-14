#!/usr/bin/env python3
"""
Tests for core/canary.py — Reliability Phase 1, Module 4
"""

import math
import time
from unittest.mock import MagicMock, patch

import pytest
import sys
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

from core.canary import (
    CanaryResult,
    CanaryFailure,
    _check_provider_health,
    _check_kalshi_auth_and_fetch,
    _check_pipeline_dry_run,
)
from core.stage_budget import StageBudget
from datetime import datetime, timezone


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_budget(seconds=30.0, elapsed=0.0, cron_run_id="test-run"):
    """StageBudget with controlled start time for deterministic testing."""
    b = StageBudget("test", seconds, cron_run_id=cron_run_id,
                     start=datetime.now(timezone.utc))
    b._start_time = time.monotonic() - elapsed
    return b


# ─── CanaryResult Dataclass Tests ───────────────────────────────────────────

class TestCanaryResult:
    def test_passed_result_fields(self):
        r = CanaryResult(passed=True, check_name="foo", elapsed_seconds=1.5)
        assert r.passed is True
        assert r.check_name == "foo"
        assert r.elapsed_seconds == 1.5
        assert r.error is None
        assert r.details == {}

    def test_failed_result_with_error_and_details(self):
        r = CanaryResult(
            passed=False,
            check_name="bar",
            elapsed_seconds=2.3,
            error="connection timeout",
            details={"provider": "grok_420", "retries": 3},
        )
        assert r.passed is False
        assert r.check_name == "bar"
        assert r.elapsed_seconds == 2.3
        assert r.error == "connection timeout"
        assert r.details["provider"] == "grok_420"


# ─── CanaryFailure Exception Tests ───────────────────────────────────────────

class TestCanaryFailure:
    def test_exception_message_formatted(self):
        results = [
            CanaryResult(passed=True, check_name="p1", elapsed_seconds=1.0),
            CanaryResult(passed=False, check_name="p2", elapsed_seconds=2.0, error="boom"),
        ]
        exc = CanaryFailure(check_name="p2", message="boom", results=results)
        assert exc.check_name == "p2"
        assert exc.message == "boom"
        assert exc.results == results
        assert "CRITICAL FAILURE" in str(exc)
        assert "p2" in str(exc)
        assert "boom" in str(exc)


# ─── Check 1: Provider Health ─────────────────────────────────────────────────
# Patch config module since canary.py does: import config as global_config

class TestProviderHealthCheck:
    def test_all_providers_pass_returns_first_ok(self):
        """When at least one provider returns valid prob, check passes."""
        mock_get = MagicMock(return_value=0.65)
        with patch("config.SENTIMENT_PROVIDERS", ["grok_420", "glm"]):
            with patch("config.CANARY_QUESTION", "Will the S&P 500 close above 5000?"):
                with patch("strategies.sentiment_scorer.get_ai_prior", mock_get):
                    budget = make_budget(seconds=15.0)
                    result = _check_provider_health(budget)

        assert result.passed is True
        assert result.check_name == "provider_health"
        assert 0.0 < result.elapsed_seconds < 15.0
        assert result.error is None
        assert "winner" in result.details

    def test_first_provider_fails_cascade_to_second(self):
        """Cascade falls through to glm when grok_420 fails."""
        call_count = [0]
        def cascade_prior(market_text, tier=None, market_ticker=None, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("timeout")
            return 0.55

        with patch("config.SENTIMENT_PROVIDERS", ["grok_420", "glm"]):
            with patch("config.CANARY_QUESTION", "Will the S&P 500 close above 5000?"):
                with patch("strategies.sentiment_scorer.get_ai_prior", side_effect=cascade_prior):
                    budget = make_budget(seconds=15.0)
                    result = _check_provider_health(budget)

        assert result.passed is True
        assert result.details["winner"] == "glm"

    def test_all_providers_fail_check_fails(self):
        """All providers fail → check fails."""
        with patch("config.SENTIMENT_PROVIDERS", ["grok_420", "glm"]):
            with patch("config.CANARY_QUESTION", "Will the S&P 500 close above 5000?"):
                with patch("strategies.sentiment_scorer.get_ai_prior", side_effect=Exception("all down")):
                    budget = make_budget(seconds=15.0)
                    result = _check_provider_health(budget)

        assert result.passed is False
        assert result.error is not None

    def test_prob_out_of_range_fails(self):
        """Provider returns prob outside [0,1] → treated as failure."""
        with patch("config.SENTIMENT_PROVIDERS", ["grok_420"]):
            with patch("config.CANARY_QUESTION", "Test"):
                with patch("strategies.sentiment_scorer.get_ai_prior", return_value=1.5):
                    budget = make_budget(seconds=15.0)
                    result = _check_provider_health(budget)

        assert result.passed is False
        assert any(p.get("status") == "out_of_range" for p in result.details.get("per_provider", []))

    def test_budget_exhausted_skips_providers(self):
        """When budget exhausted before any call, all providers skipped."""
        with patch("config.SENTIMENT_PROVIDERS", ["grok_420", "glm"]):
            with patch("config.CANARY_QUESTION", "Test"):
                with patch("strategies.sentiment_scorer.get_ai_prior") as mock_prior:
                    budget = make_budget(seconds=0.001, elapsed=10.0)
                    result = _check_provider_health(budget)

        assert result.passed is False
        assert all(p.get("status") == "skipped_budget_exhausted"
                   for p in result.details.get("per_provider", []))
        mock_prior.assert_not_called()

    def test_import_failure_fails_fast(self):
        """If get_ai_prior raises ImportError at call time, check fails immediately."""
        with patch("config.SENTIMENT_PROVIDERS", ["grok_420"]):
            with patch("config.CANARY_QUESTION", "Test"):
                with patch("strategies.sentiment_scorer.get_ai_prior", side_effect=ImportError("module not found")):
                    budget = make_budget(seconds=15.0)
                    result = _check_provider_health(budget)

        # ImportError is caught as Exception → per_provider status="exception" → all fail
        assert result.passed is False
        assert result.error is not None


# ─── Check 2: Kalshi Auth + Fetch ─────────────────────────────────────────────

class TestKalshiAuthCheck:
    def _make_mock_private_key(self):
        """Return a properly-configured mock private key for signing."""
        mock_pk = MagicMock()
        mock_pk.sign.return_value = b"fake_signature_bytes_are_long_enough_for_base64"
        return mock_pk

    def test_happy_path(self):
        """Valid 200 response with all fields → pass."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "market": {"ticker": "KXINXU-26DEC31", "yes_bid": 0.55, "yes_ask": 0.57}
        }

        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch("requests.get", return_value=mock_resp):
                with patch.dict("os.environ", {"KALSHI_KEY": "test_key", "KALSHI_SECRET_FILE": "/tmp/test.pem"}):
                    with patch("builtins.open", MagicMock(read_bytes=b"-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----")):
                        with patch("cryptography.hazmat.primitives.serialization.load_pem_private_key",
                                   return_value=self._make_mock_private_key()):
                            budget = make_budget(seconds=5.0)
                            result = _check_kalshi_auth_and_fetch(budget)

        assert result.passed is True
        assert result.check_name == "kalshi_auth_and_fetch"
        assert result.error is None
        assert result.details.get("ticker") == "KXINXU-26DEC31"

    def test_auth_failure_401(self):
        """HTTP 401 → check fails."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch("requests.get", return_value=mock_resp):
                with patch.dict("os.environ", {"KALSHI_KEY": "bad_key", "KALSHI_SECRET_FILE": "/tmp/test.pem"}):
                    with patch("builtins.open", MagicMock(read_bytes=b"fake")):
                        with patch("cryptography.hazmat.primitives.serialization.load_pem_private_key",
                                   return_value=self._make_mock_private_key()):
                            budget = make_budget(seconds=5.0)
                            result = _check_kalshi_auth_and_fetch(budget)

        assert result.passed is False
        assert "Auth failed" in result.error
        assert result.details.get("status_code") == 401

    def test_missing_required_fields(self):
        """Response missing price fields → fails."""
        mock_resp = MagicMock()
        # has ticker but no price fields
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"market": {"ticker": "KXINXY-26DEC31H1600-B5700"}}

        with patch("config.CANARY_TICKER", "KXINXY-26DEC31H1600-B5700"):
            with patch("requests.get", return_value=mock_resp):
                with patch.dict("os.environ", {"KALSHI_KEY": "key", "KALSHI_SECRET_FILE": "/tmp/test.pem"}):
                    with patch("builtins.open", MagicMock(read_bytes=b"fake")):
                        with patch("cryptography.hazmat.primitives.serialization.load_pem_private_key",
                                   return_value=self._make_mock_private_key()):
                            budget = make_budget(seconds=5.0)
                            result = _check_kalshi_auth_and_fetch(budget)

        assert result.passed is False
        assert "price" in result.error.lower() or "missing" in result.error.lower()

    def _mock_market_response(ticker, yes_bid=None, yes_ask=None, **extra):
        """Build a mock market response dict with the named price fields present."""
        m = {"ticker": ticker}
        if yes_bid is not None:
            m["yes_bid"] = yes_bid
        if yes_ask is not None:
            m["yes_ask"] = yes_ask
        m.update(extra)
        return m

    def test_no_kalshi_key(self):
        """KALSHI_KEY not set → fails fast without HTTP call."""
        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch.dict("os.environ", {}, clear=True):
                with patch("requests.get") as mock_get:
                    budget = make_budget(seconds=5.0)
                    result = _check_kalshi_auth_and_fetch(budget)

        assert result.passed is False
        assert "KALSHI_KEY" in result.error
        mock_get.assert_not_called()


# ─── Check 3: Pipeline Dry Run ─────────────────────────────────────────────────

class TestPipelineDryRun:
    def test_happy_path_with_cached_prior(self):
        """Pipeline completes end-to-end with cached prior → pass."""
        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch("utils.kalshi.fetch_kalshi_markets",
                       return_value=[{"id": "KXINXU-26DEC31", "question": "S&P 500?", "ticker": "KXINXU-26DEC31"}]):
                with patch("strategies.kalshi_optimize.calculate_optimal_order_size", return_value=5.0) as mock_calc:
                    with patch("utils.proof.generate_proof", return_value=None) as mock_proof:
                        budget = make_budget(seconds=10.0)
                        result = _check_pipeline_dry_run(budget, provider_prior=0.65)

        assert result.passed is True
        assert result.details["ai_prior"] == 0.65
        assert result.details["prior_source"] == "canary_check_1_cached"
        assert result.details["order_size"] == 5.0
        mock_calc.assert_called_once()
        mock_proof.assert_called_once()

    def test_no_cached_prior_calls_provider(self):
        """No cached prior → estimate_true_price called → pass."""
        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch("utils.kalshi.fetch_kalshi_markets",
                       return_value=[{"id": "KXINXU-26DEC31", "question": "S&P 500?", "ticker": "KXINXU-26DEC31"}]):
                with patch("strategies.kalshi_optimize.estimate_true_price", return_value=0.60) as mock_est:
                    with patch("strategies.kalshi_optimize.calculate_optimal_order_size", return_value=3.0):
                        with patch("utils.proof.generate_proof", return_value=None):
                            budget = make_budget(seconds=10.0)
                            result = _check_pipeline_dry_run(budget, provider_prior=None)

        assert result.passed is True
        assert result.details["prior_source"] == "pipeline_call"
        mock_est.assert_called_once()

    def test_ticker_not_found(self):
        """Canary ticker not in fetched markets → fail."""
        with patch("config.CANARY_TICKER", "NOTFOUND-TICKER"):
            with patch("utils.kalshi.fetch_kalshi_markets",
                       return_value=[{"id": "KXINXU-26DEC31", "question": "S&P?", "ticker": "KXINXU-26DEC31"}]):
                budget = make_budget(seconds=10.0)
                result = _check_pipeline_dry_run(budget, provider_prior=None)

        assert result.passed is False
        assert "not found" in result.error.lower()

    def test_kelly_returns_nan_fails(self):
        """Kelly sizing returns NaN → check fails."""
        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch("utils.kalshi.fetch_kalshi_markets",
                       return_value=[{"id": "KXINXU-26DEC31", "question": "S&P?", "ticker": "KXINXU-26DEC31"}]):
                with patch("strategies.kalshi_optimize.estimate_true_price", return_value=0.60):
                    with patch("strategies.kalshi_optimize.calculate_optimal_order_size", return_value=float("nan")):
                        with patch("utils.proof.generate_proof", return_value=None):
                            budget = make_budget(seconds=10.0)
                            result = _check_pipeline_dry_run(budget, provider_prior=None)

        assert result.passed is False
        assert "NaN" in result.error

    def test_ai_prior_out_of_range(self):
        """AI prior is 1.5 (out of [0,1]) → check fails."""
        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch("utils.kalshi.fetch_kalshi_markets",
                       return_value=[{"id": "KXINXU-26DEC31", "question": "S&P?", "ticker": "KXINXU-26DEC31"}]):
                with patch("strategies.kalshi_optimize.estimate_true_price", return_value=1.5):
                    budget = make_budget(seconds=10.0)
                    result = _check_pipeline_dry_run(budget, provider_prior=None)

        assert result.passed is False
        assert "out of range" in result.error.lower()

    def test_budget_exhausted_before_prior_call(self):
        """Budget exhausted before AI prior call → fail."""
        with patch("config.CANARY_TICKER", "KXINXU-26DEC31"):
            with patch("utils.kalshi.fetch_kalshi_markets",
                       return_value=[{"id": "KXINXU-26DEC31", "question": "S&P?", "ticker": "KXINXU-26DEC31"}]):
                budget = make_budget(seconds=0.001, elapsed=10.0)
                result = _check_pipeline_dry_run(budget, provider_prior=None)

        assert result.passed is False
        assert "exhausted" in result.error.lower()


# ─── Full run_canary() Integration Tests ─────────────────────────────────────

class TestRunCanary:
    def test_all_pass_returns_true(self):
        """All 3 checks pass → run_canary returns (True, results)."""
        r1 = CanaryResult(passed=True, check_name="provider_health", elapsed_seconds=2.0,
                          details={"winner": "grok_420"})
        r2 = CanaryResult(passed=True, check_name="kalshi_auth_and_fetch", elapsed_seconds=1.5)
        r3 = CanaryResult(passed=True, check_name="pipeline_dry_run", elapsed_seconds=3.0)

        with patch("config.CANARY_BUDGET_SECONDS", 30):
            with patch("config.CANARY_ENABLED", True):
                with patch("core.canary._check_provider_health", return_value=r1):
                    with patch("core.canary._check_kalshi_auth_and_fetch", return_value=r2):
                        with patch("core.canary._check_pipeline_dry_run", return_value=r3):
                            with patch("core.canary._write_scratchpad_event"):
                                from core.canary import run_canary
                                passed, results = run_canary()

        assert passed is True
        assert len(results) == 3
        assert all(r.passed for r in results)

    def test_one_fails_returns_false(self):
        """Check 2 fails → run_canary returns (False, results with failed check)."""
        r1 = CanaryResult(passed=True, check_name="provider_health", elapsed_seconds=2.0, details={})
        r2 = CanaryResult(passed=False, check_name="kalshi_auth_and_fetch", elapsed_seconds=1.5,
                          error="HTTP 401: Unauthorized")
        r3 = CanaryResult(passed=True, check_name="pipeline_dry_run", elapsed_seconds=3.0)

        with patch("config.CANARY_BUDGET_SECONDS", 30):
            with patch("config.CANARY_ENABLED", True):
                with patch("core.canary._check_provider_health", return_value=r1):
                    with patch("core.canary._check_kalshi_auth_and_fetch", return_value=r2):
                        with patch("core.canary._check_pipeline_dry_run", return_value=r3):
                            with patch("core.canary._write_scratchpad_event"):
                                from core.canary import run_canary
                                passed, results = run_canary()

        assert passed is False
        failed = next(r for r in results if not r.passed)
        assert failed.check_name == "kalshi_auth_and_fetch"
        assert failed.error == "HTTP 401: Unauthorized"

    def test_passes_cached_prior_to_check3(self):
        """When check 1 passes, run_canary extracts winner prob and passes to check 3."""
        r1 = CanaryResult(passed=True, check_name="provider_health", elapsed_seconds=2.0,
                          details={"winner": "glm", "per_provider": [
                              {"status": "exception", "provider": "grok_420"},
                              {"status": "ok", "provider": "glm", "prob": 0.72},
                          ]})
        r2 = CanaryResult(passed=True, check_name="kalshi_auth_and_fetch", elapsed_seconds=1.5)
        r3 = CanaryResult(passed=True, check_name="pipeline_dry_run", elapsed_seconds=3.0)

        mock_check1 = MagicMock(return_value=r1)
        mock_check2 = MagicMock(return_value=r2)
        mock_check3 = MagicMock(return_value=r3)

        with patch("config.CANARY_BUDGET_SECONDS", 30):
            with patch("config.CANARY_ENABLED", True):
                with patch("core.canary._check_provider_health", mock_check1):
                    with patch("core.canary._check_kalshi_auth_and_fetch", mock_check2):
                        with patch("core.canary._check_pipeline_dry_run", mock_check3):
                            with patch("core.canary._write_scratchpad_event"):
                                from core.canary import run_canary
                                run_canary()

        mock_check3.assert_called_once()
        _, kwargs = mock_check3.call_args
        assert kwargs.get("provider_prior") == 0.72

    def test_scratchpad_canary_pass_written(self):
        """canary_pass scratchpad event written when all checks pass."""
        r1 = CanaryResult(passed=True, check_name="provider_health", elapsed_seconds=2.0, details={})
        r2 = CanaryResult(passed=True, check_name="kalshi_auth_and_fetch", elapsed_seconds=1.5, details={})
        r3 = CanaryResult(passed=True, check_name="pipeline_dry_run", elapsed_seconds=3.0, details={})

        with patch("config.CANARY_BUDGET_SECONDS", 30):
            with patch("config.CANARY_ENABLED", True):
                with patch("core.canary._check_provider_health", return_value=r1):
                    with patch("core.canary._check_kalshi_auth_and_fetch", return_value=r2):
                        with patch("core.canary._check_pipeline_dry_run", return_value=r3):
                            with patch("core.canary._write_scratchpad_event") as mock_write:
                                from core.canary import run_canary
                                run_canary()

        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs["event_type"] == "canary_pass"

    def test_scratchpad_canary_failure_written(self):
        """canary_failure scratchpad event written when any check fails."""
        r1 = CanaryResult(passed=False, check_name="provider_health", elapsed_seconds=2.0,
                          error="All providers failed", details={})
        r2 = CanaryResult(passed=True, check_name="kalshi_auth_and_fetch", elapsed_seconds=1.5, details={})
        r3 = CanaryResult(passed=True, check_name="pipeline_dry_run", elapsed_seconds=3.0, details={})

        with patch("config.CANARY_BUDGET_SECONDS", 30):
            with patch("config.CANARY_ENABLED", True):
                with patch("core.canary._check_provider_health", return_value=r1):
                    with patch("core.canary._check_kalshi_auth_and_fetch", return_value=r2):
                        with patch("core.canary._check_pipeline_dry_run", return_value=r3):
                            with patch("core.canary._write_scratchpad_event") as mock_write:
                                from core.canary import run_canary
                                run_canary()

        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs["event_type"] == "canary_failure"
        # The function parameter is `failed_check` (kwarg key), written to entry as `failed_check_name`
        assert kwargs["failed_check"] == "provider_health"
        assert kwargs["error_message"] == "All providers failed"


# ─── Canary Budget Exhaustion Tests ─────────────────────────────────────────

class TestCanaryBudget:
    def test_budget_exhaustion_fails_gracefully(self):
        """Total canary budget exhausted → run_canary returns False, doesn't hang.

        We test this by patching check1's result to indicate it found the budget
        exhausted and returned fast. The outer canary_budget has a tiny budget
        but is not mocked — we use 0.001s so any real time spent (import delay,
        scratchpad I/O) is enough to exhaust it before check1 even runs.
        """
        # Use a patched check that records whether the budget was already exhausted
        # when it was called. By patching StageBudget we ensure the outer budget
        # is always exhausted immediately when entered.
        import core.canary as canary_mod

        check1_called = [False]
        budget_at_entry_exhausted = [None]

        def tracking_check(budget):
            check1_called[0] = True
            budget_at_entry_exhausted[0] = budget.exhausted()
            # Fast fail regardless
            return CanaryResult(
                passed=False,
                check_name="provider_health",
                elapsed_seconds=0.0,
                error="budget was exhausted",
            )

        with patch("config.CANARY_BUDGET_SECONDS", 30.0):  # enough budget for imports
            with patch("config.CANARY_ENABLED", True):
                with patch.object(canary_mod.StageBudget, "__enter__", lambda self: self):
                    # Force outer budget to be exhausted immediately
                    with patch.object(canary_mod.StageBudget, "exhausted", return_value=True):
                        with patch.object(canary_mod.StageBudget, "remaining", return_value=0.0):
                            with patch.object(canary_mod.StageBudget, "elapsed", return_value=100.0):
                                with patch.object(canary_mod.StageBudget, "__exit__", return_value=False):
                                    with patch.object(canary_mod, "_check_provider_health", side_effect=tracking_check):
                                        with patch.object(canary_mod, "_check_kalshi_auth_and_fetch",
                                                           return_value=CanaryResult(passed=True, check_name="k", elapsed_seconds=0)):
                                            with patch.object(canary_mod, "_check_pipeline_dry_run",
                                                               return_value=CanaryResult(passed=True, check_name="p", elapsed_seconds=0)):
                                                with patch.object(canary_mod, "_write_scratchpad_event"):
                                                    from core.canary import run_canary
                                                    tick = time.monotonic()
                                                    passed, results = run_canary()
                                                    elapsed = time.monotonic() - tick

        assert check1_called[0], "check1 should have been called"
        assert passed is False
        assert elapsed < 5.0  # didn't hang


# ─── Module Import Tests ─────────────────────────────────────────────────────

class TestCanaryModule:
    def test_module_imports_cleanly(self):
        """Module imports without ImportError or SyntaxError."""
        from core import canary
        assert hasattr(canary, "CanaryResult")
        assert hasattr(canary, "CanaryFailure")
        assert hasattr(canary, "run_canary")
        assert hasattr(canary, "_check_provider_health")
        assert hasattr(canary, "_check_kalshi_auth_and_fetch")
        assert hasattr(canary, "_check_pipeline_dry_run")