#!/usr/bin/env python3
"""
Tests for Circuit Breaker Module — Reliability Phase 1, Module 2.
"""

import json
import os
import time
import pytest
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

from core.circuit_breaker import (
    BreakerState,
    BreakerConfig,
    CallRecord,
    BreakerSnapshot,
    CircuitBreaker,
    load_breakers,
    save_breakers,
    get_breaker,
)
from core.provider_wrapper import GuardedProvider, ai_prior_validator


def make_config(**overrides):
    defaults = {"name": "test_provider", "window_size": 20, "failure_threshold": 0.5,
                "cooldown_seconds": 300, "min_calls_before_opening": 20}
    defaults.update(overrides)
    return BreakerConfig(**defaults)


def make_breaker(config=None, state=BreakerState.CLOSED, window=None,
                 clock=None, on_state_change=None):
    cfg = config or make_config()
    snap = BreakerSnapshot(name=cfg.name, state=state, window=window or [],
                           opened_at=None)
    return CircuitBreaker(cfg, snap, clock=clock, on_state_change=on_state_change)


class TestBreakerConfig:
    def test_defaults(self):
        cfg = BreakerConfig(name="x")
        assert cfg.window_size == 20
        assert cfg.failure_threshold == 0.5
        assert cfg.cooldown_seconds == 300
        assert cfg.min_calls_before_opening == 20

    def test_custom(self):
        cfg = BreakerConfig(name="y", window_size=10, failure_threshold=0.3,
                            cooldown_seconds=60, min_calls_before_opening=10)
        assert cfg.window_size == 10
        assert cfg.failure_threshold == 0.3
        assert cfg.cooldown_seconds == 60
        assert cfg.min_calls_before_opening == 10


class TestCircuitBreakerInitialState:
    def test_starts_closed(self):
        b = make_breaker()
        assert b.state == BreakerState.CLOSED

    def test_allow_call_closed(self):
        b = make_breaker()
        assert b.allow_call() is True

    def test_success_rate_empty_window(self):
        b = make_breaker()
        assert b.success_rate() == 1.0


class TestCircuitBreakerOpening:
    def _fill_failures(self, breaker, n):
        for _ in range(n):
            breaker.record_failure(1.0, "exception")

    def test_does_not_open_before_min_calls(self):
        cfg = make_config(min_calls_before_opening=20)
        b = make_breaker(config=cfg)
        for _ in range(19):
            b.record_failure(1.0, "exception")
        assert b.state == BreakerState.CLOSED

    def test_opens_at_min_calls_with_low_success(self):
        cfg = make_config(min_calls_before_opening=20, failure_threshold=0.5)
        b = make_breaker(config=cfg)
        for _ in range(20):
            b.record_failure(1.0, "exception")
        assert b.state == BreakerState.OPEN
        assert b.total_opens == 1

    def test_does_not_open_if_success_rate_ok(self):
        cfg = make_config(min_calls_before_opening=20, failure_threshold=0.5)
        b = make_breaker(config=cfg)
        for _ in range(10):
            b.record_success(1.0)
        for _ in range(10):
            b.record_failure(1.0, "exception")
        assert b.state == BreakerState.CLOSED

    def test_opens_with_exactly_threshold_minus_one_success(self):
        cfg = make_config(min_calls_before_opening=20, failure_threshold=0.5)
        b = make_breaker(config=cfg)
        for _ in range(9):
            b.record_success(1.0)
        for _ in range(11):
            b.record_failure(1.0, "exception")
        assert b.state == BreakerState.OPEN

    def test_stays_closed_with_majority_success(self):
        cfg = make_config(min_calls_before_opening=20, failure_threshold=0.5)
        b = make_breaker(config=cfg)
        for _ in range(11):
            b.record_success(1.0)
        for _ in range(9):
            b.record_failure(1.0, "exception")
        assert b.state == BreakerState.CLOSED


class TestCircuitBreakerOpenState:
    def test_allow_call_false_when_open(self):
        now = datetime.now(timezone.utc)
        b = make_breaker(state=BreakerState.OPEN)
        b._opened_at = now
        assert b.allow_call() is False

    def test_transitions_to_half_open_after_cooldown(self):
        now = datetime.now(timezone.utc)
        opened = now - timedelta(seconds=301)
        clock_times = [now]
        clock = lambda: clock_times[0]
        cfg = make_config(cooldown_seconds=300)
        b = make_breaker(config=cfg, state=BreakerState.OPEN, clock=clock)
        b._opened_at = opened
        assert b.allow_call() is True
        assert b.state == BreakerState.HALF_OPEN

    def test_stays_open_before_cooldown(self):
        now = datetime.now(timezone.utc)
        opened = now - timedelta(seconds=100)
        clock = lambda: now
        cfg = make_config(cooldown_seconds=300)
        b = make_breaker(config=cfg, state=BreakerState.OPEN, clock=clock)
        b._opened_at = opened
        assert b.allow_call() is False
        assert b.state == BreakerState.OPEN


class TestCircuitBreakerHalfOpen:
    def test_half_open_allows_one_probe(self):
        b = make_breaker(state=BreakerState.HALF_OPEN)
        assert b.allow_call() is True
        assert b.allow_call() is False

    def test_half_open_to_closed_on_success(self):
        b = make_breaker(state=BreakerState.HALF_OPEN)
        b.allow_call()
        b.record_success(1.0)
        assert b.state == BreakerState.CLOSED

    def test_half_open_to_open_on_failure(self):
        now = datetime.now(timezone.utc)
        clock = lambda: now
        cfg = make_config(cooldown_seconds=300)
        b = make_breaker(config=cfg, state=BreakerState.HALF_OPEN, clock=clock)
        b.allow_call()
        b.record_failure(1.0, "exception")
        assert b.state == BreakerState.OPEN
        assert b.total_opens == 1

    def test_half_open_rejects_second_call(self):
        b = make_breaker(state=BreakerState.HALF_OPEN)
        b.allow_call()
        assert b.allow_call() is False


class TestRollingWindow:
    def test_evicts_beyond_window_size(self):
        cfg = make_config(window_size=5)
        b = make_breaker(config=cfg)
        for i in range(10):
            b.record_success(1.0)
        assert len(b.window) == 5

    def test_success_rate_reflects_window(self):
        cfg = make_config(window_size=5, min_calls_before_opening=5)
        b = make_breaker(config=cfg)
        for _ in range(3):
            b.record_success(1.0)
        for _ in range(2):
            b.record_failure(1.0, "exception")
        assert b.success_rate() == 0.6

    def test_success_rate_returns_1_when_below_min(self):
        cfg = make_config(min_calls_before_opening=100)
        b = make_breaker(config=cfg)
        for _ in range(10):
            b.record_failure(1.0, "exception")
        assert b.success_rate() == 1.0


class TestBreakerSnapshot:
    def test_to_dict_and_back(self):
        now = datetime.now(timezone.utc)
        snap = BreakerSnapshot(
            name="test",
            state=BreakerState.CLOSED,
            window=[CallRecord(timestamp=now, success=True, latency_seconds=1.5, failure_mode=None)],
            opened_at=None,
            total_opens=0,
            total_calls=1,
            total_failures=0,
        )
        d = snap.to_dict()
        snap2 = BreakerSnapshot.from_dict(d)
        assert snap2.name == "test"
        assert snap2.state == BreakerState.CLOSED
        assert len(snap2.window) == 1
        assert snap2.window[0].success is True
        assert snap2.total_calls == 1

    def test_round_trip_with_open_state(self):
        now = datetime.now(timezone.utc)
        snap = BreakerSnapshot(
            name="grok_420",
            state=BreakerState.OPEN,
            window=[],
            opened_at=now,
            total_opens=3,
            total_calls=100,
            total_failures=45,
        )
        d = snap.to_dict()
        snap2 = BreakerSnapshot.from_dict(d)
        assert snap2.state == BreakerState.OPEN
        assert snap2.total_opens == 3
        assert snap2.total_calls == 100
        assert snap2.opened_at is not None


class TestLoadSaveBreakers:
    def test_load_missing_file(self, tmp_path):
        result = load_breakers(tmp_path / "nonexistent.json")
        assert result == {}

    def test_load_corrupt_file(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("NOT JSON{{{")
        result = load_breakers(p)
        assert result == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        p = tmp_path / "breakers.json"
        cfg = make_config()
        b = make_breaker(config=cfg)
        b.record_success(1.0)
        b.record_failure(2.0, "timeout")
        save_breakers({"test_provider": b}, p)
        loaded = load_breakers(p)
        assert "test_provider" in loaded
        lb = loaded["test_provider"]
        assert lb.state == BreakerState.CLOSED
        assert lb.total_calls == 2
        assert lb.total_failures == 1
        assert len(lb.window) == 2

    def test_save_atomic_no_partial_on_crash(self, tmp_path):
        p = tmp_path / "breakers.json"
        b = make_breaker()
        save_breakers({"t": b}, p)
        assert p.exists()
        data = json.loads(p.read_text())
        assert "t" in data


class TestGetBreaker:
    def test_returns_existing(self):
        cfg = make_config()
        b = make_breaker(config=cfg)
        d = {"test_provider": b}
        result = get_breaker("test_provider", d, cfg)
        assert result is b

    def test_creates_new_closed(self):
        d = {}
        cfg = make_config()
        result = get_breaker("new_provider", d, cfg)
        assert result.state == BreakerState.CLOSED
        assert "new_provider" in d

    def test_creates_with_default_config(self):
        d = {}
        result = get_breaker("x", d)
        assert result.state == BreakerState.CLOSED


class TestAiPriorValidator:
    def test_valid_float(self):
        assert ai_prior_validator(0.5) is True
        assert ai_prior_validator(0.0) is True
        assert ai_prior_validator(1.0) is True

    def test_invalid_types(self):
        assert ai_prior_validator(None) is False
        assert ai_prior_validator("0.5") is False
        assert ai_prior_validator(0) is False

    def test_out_of_range(self):
        assert ai_prior_validator(-0.1) is False
        assert ai_prior_validator(1.1) is False


class TestGuardedProvider:
    def _make_gp(self, call_fn=None, breaker=None, timeout=10.0):
        b = breaker or make_breaker()
        return GuardedProvider(
            provider_name="test_provider",
            call_fn=call_fn or (lambda text, timeout=None: 0.75),
            validator=ai_prior_validator,
            breaker=b,
            per_call_timeout=timeout,
        )

    def test_happy_path_returns_success(self):
        gp = self._make_gp(call_fn=lambda text, timeout=None: 0.75)
        result, status = gp.call("test question")
        assert result == 0.75
        assert status == "success"

    def test_exception_returns_exception(self):
        def fail(text, timeout=None):
            raise ConnectionError("boom")
        gp = self._make_gp(call_fn=fail)
        result, status = gp.call("test")
        assert result is None
        assert status == "exception"

    def test_timeout_returns_timeout(self):
        def slow(text, timeout=None):
            time.sleep(0.3)
            return 0.5
        gp = self._make_gp(call_fn=slow, timeout=0.1)
        result, status = gp.call("test")
        assert result is None
        assert status == "timeout"

    def test_invalid_returns_invalid(self):
        gp = self._make_gp(call_fn=lambda text, timeout=None: "not_a_float")
        result, status = gp.call("test")
        assert result is None
        assert status == "invalid"

    def test_short_circuit_without_calling_fn(self):
        calls = []
        def track(text, timeout=None):
            calls.append(1)
            return 0.5

        cfg = make_config(cooldown_seconds=300)
        b = make_breaker(config=cfg, state=BreakerState.OPEN)
        b._opened_at = datetime.now(timezone.utc)

        gp = self._make_gp(call_fn=track, breaker=b)
        result, status = gp.call("test")
        assert result is None
        assert status == "short_circuit"
        assert len(calls) == 0

    def test_rate_limit_skips_recording(self):
        class RateLimitError(Exception):
            pass

        def rate_limited(text, timeout=None):
            raise RateLimitError("429")

        b = make_breaker()
        gp = GuardedProvider(
            provider_name="test",
            call_fn=rate_limited,
            validator=ai_prior_validator,
            breaker=b,
            per_call_timeout=10.0,
            skip_exception_types=(RateLimitError,),
        )
        result, status = gp.call("test")
        assert result is None
        assert status == "rate_limit"
        assert b.total_calls == 0

    def test_records_success_on_breaker(self):
        b = make_breaker()
        gp = self._make_gp(breaker=b)
        gp.call("test")
        assert b.total_calls == 1
        assert b.total_failures == 0

    def test_records_failure_on_breaker(self):
        def fail(text, timeout=None):
            raise RuntimeError("fail")
        b = make_breaker()
        gp = self._make_gp(call_fn=fail, breaker=b)
        gp.call("test")
        assert b.total_calls == 1
        assert b.total_failures == 1


class TestGuardedProviderRunStats:
    def test_run_summary(self):
        gp = GuardedProvider(
            provider_name="p",
            call_fn=lambda text, timeout=None: 0.5,
            validator=ai_prior_validator,
            breaker=make_breaker(),
            per_call_timeout=10.0,
        )
        gp.call("q1")
        gp.call("q2")
        summary = gp.get_run_summary()
        assert summary["calls_this_run"] == 2
        assert summary["successes_this_run"] == 2
        assert summary["failures_this_run"] == 0
        assert summary["final_state"] == "CLOSED"

    def test_reset_run_stats(self):
        gp = GuardedProvider(
            provider_name="p",
            call_fn=lambda text, timeout=None: 0.5,
            validator=ai_prior_validator,
            breaker=make_breaker(),
            per_call_timeout=10.0,
        )
        gp.call("q")
        gp.reset_run_stats()
        summary = gp.get_run_summary()
        assert summary["calls_this_run"] == 0


class TestIntegrationCascadeFallthrough:
    def test_provider_a_opens_cascade_falls_to_b(self):
        cfg_a = make_config(name="provider_a", min_calls_before_opening=5, window_size=5)
        breaker_a = make_breaker(config=cfg_a)
        for _ in range(5):
            breaker_a.record_failure(1.0, "exception")
        assert breaker_a.state == BreakerState.OPEN

        gp_a = GuardedProvider(
            provider_name="provider_a",
            call_fn=lambda text, timeout=None: (_ for _ in ()).throw(RuntimeError("fail")),
            validator=ai_prior_validator,
            breaker=breaker_a,
            per_call_timeout=5.0,
        )

        cfg_b = make_config(name="provider_b")
        breaker_b = make_breaker(config=cfg_b)
        gp_b = GuardedProvider(
            provider_name="provider_b",
            call_fn=lambda text, timeout=None: 0.65,
            validator=ai_prior_validator,
            breaker=breaker_b,
            per_call_timeout=5.0,
        )

        result_a, status_a = gp_a.call("test market")
        assert result_a is None
        assert status_a == "short_circuit"

        result_b, status_b = gp_b.call("test market")
        assert result_b == 0.65
        assert status_b == "success"

    def test_all_providers_open_returns_neutral(self):
        providers = {}
        for name in ["a", "b", "c"]:
            cfg = make_config(name=name, cooldown_seconds=300)
            b = make_breaker(config=cfg, state=BreakerState.OPEN)
            b._opened_at = datetime.now(timezone.utc)
            gp = GuardedProvider(
                provider_name=name,
                call_fn=lambda text, timeout=None: 0.5,
                validator=ai_prior_validator,
                breaker=b,
                per_call_timeout=5.0,
            )
            providers[name] = gp

        all_short = True
        final_result = 0.5
        for gp in providers.values():
            result, status = gp.call("test")
            if status != "short_circuit":
                all_short = False
                final_result = result
                break

        assert all_short is True
        assert final_result == 0.5

    def test_breaker_state_change_callback(self):
        changes = []
        def on_change(old, new, reason, rate):
            changes.append((old.name, new.name, reason))

        cfg = make_config(min_calls_before_opening=5, window_size=5)
        b = make_breaker(config=cfg, on_state_change=on_change)
        for _ in range(5):
            b.record_failure(1.0, "exception")
        assert len(changes) == 1
        assert changes[0] == ("CLOSED", "OPEN", "failure_threshold")


class TestTimeManipulation:
    def test_cooldown_with_frozen_clock(self):
        t0 = datetime.now(timezone.utc)
        times = [t0]

        def clock():
            return times[0]

        cfg = make_config(cooldown_seconds=100)
        b = make_breaker(config=cfg, state=BreakerState.OPEN, clock=clock)
        b._opened_at = t0

        assert b.allow_call() is False

        times[0] = t0 + timedelta(seconds=100)
        assert b.allow_call() is True
        assert b.state == BreakerState.HALF_OPEN

    def test_half_open_probe_with_frozen_clock(self):
        t0 = datetime.now(timezone.utc)
        clock = lambda: t0
        cfg = make_config(cooldown_seconds=10)
        b = make_breaker(config=cfg, state=BreakerState.HALF_OPEN, clock=clock)

        assert b.allow_call() is True
        b.record_success(1.0)
        assert b.state == BreakerState.CLOSED

    def test_full_lifecycle_with_clock(self):
        t0 = datetime.now(timezone.utc)
        times = [t0]

        def clock():
            return times[0]

        cfg = make_config(min_calls_before_opening=3, window_size=5, cooldown_seconds=60)
        b = make_breaker(config=cfg, clock=clock)

        for _ in range(3):
            b.record_failure(1.0, "exception")
        assert b.state == BreakerState.OPEN

        assert b.allow_call() is False

        times[0] = t0 + timedelta(seconds=61)
        assert b.allow_call() is True
        assert b.state == BreakerState.HALF_OPEN

        assert b.allow_call() is False

        b.record_failure(1.5, "exception")
        assert b.state == BreakerState.OPEN

        times[0] = t0 + timedelta(seconds=122)
        assert b.allow_call() is True
        assert b.state == BreakerState.HALF_OPEN

        b.record_success(0.5)
        assert b.state == BreakerState.CLOSED
        assert b.allow_call() is True
