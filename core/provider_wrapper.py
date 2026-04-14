#!/usr/bin/env python3
"""
Provider Wrapper — Guards provider calls with circuit breaker + timeout + validation.
Reliability Phase 1, Module 2.
"""

import logging
import time
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


def ai_prior_validator(result: Any) -> bool:
    return isinstance(result, float) and 0.0 <= result <= 1.0


class GuardedProvider:
    def __init__(
        self,
        provider_name: str,
        call_fn: Callable,
        validator: Callable,
        breaker,
        per_call_timeout: float,
        skip_exception_types: Optional[tuple] = None,
    ):
        self.provider_name = provider_name
        self.call_fn = call_fn
        self.validator = validator
        self.breaker = breaker
        self.per_call_timeout = per_call_timeout
        self.skip_exception_types = skip_exception_types or ()

        self._run_calls = 0
        self._run_successes = 0
        self._run_failures = 0
        self._run_short_circuits = 0
        self._run_latencies = []

    def call(self, *args, **kwargs) -> Tuple[Optional[Any], str]:
        if not self.breaker.allow_call():
            self._run_short_circuits += 1
            return None, "short_circuit"

        effective_timeout = kwargs.get("timeout") or self.per_call_timeout
        kwargs.setdefault("timeout", effective_timeout)

        start = time.monotonic()
        try:
            result = self.call_fn(*args, **kwargs)
        except tuple(self.skip_exception_types):
            return None, "rate_limit"
        except Exception:
            elapsed = time.monotonic() - start
            mode = "timeout" if elapsed >= effective_timeout else "exception"
            self.breaker.record_failure(elapsed, mode)
            self._run_calls += 1
            self._run_failures += 1
            self._run_latencies.append(elapsed)
            return None, mode

        elapsed = time.monotonic() - start

        if elapsed >= effective_timeout:
            self.breaker.record_failure(elapsed, "timeout")
            self._run_calls += 1
            self._run_failures += 1
            self._run_latencies.append(elapsed)
            return None, "timeout"

        if self.validator(result):
            self.breaker.record_success(elapsed)
            self._run_calls += 1
            self._run_successes += 1
            self._run_latencies.append(elapsed)
            return result, "success"
        else:
            self.breaker.record_failure(elapsed, "invalid")
            self._run_calls += 1
            self._run_failures += 1
            self._run_latencies.append(elapsed)
            return None, "invalid"

    def get_run_summary(self) -> dict:
        avg_latency = (
            sum(self._run_latencies) / len(self._run_latencies)
            if self._run_latencies
            else 0.0
        )
        return {
            "provider_name": self.provider_name,
            "calls_this_run": self._run_calls,
            "successes_this_run": self._run_successes,
            "failures_this_run": self._run_failures,
            "short_circuits_this_run": self._run_short_circuits,
            "avg_latency_seconds": round(avg_latency, 4),
            "final_state": self.breaker.state.name,
            "success_rate": round(self.breaker.success_rate(), 4),
        }

    def reset_run_stats(self) -> None:
        self._run_calls = 0
        self._run_successes = 0
        self._run_failures = 0
        self._run_short_circuits = 0
        self._run_latencies = []
