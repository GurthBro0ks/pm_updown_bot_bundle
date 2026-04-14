#!/usr/bin/env python3
"""
Circuit Breaker Module — Per-provider automatic circuit breakers.
Reliability Phase 1, Module 2.

Three states per breaker: CLOSED (normal), OPEN (short-circuit), HALF_OPEN (probe).
Rolling window of last N calls. Transitions based on success rate.
State persists across cron runs.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class BreakerState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class BreakerConfig:
    name: str
    window_size: int = 20
    failure_threshold: float = 0.5
    cooldown_seconds: int = 300
    min_calls_before_opening: int = 20


@dataclass
class CallRecord:
    timestamp: datetime
    success: bool
    latency_seconds: float
    failure_mode: Optional[str] = None


@dataclass
class BreakerSnapshot:
    name: str
    state: BreakerState
    window: list
    opened_at: Optional[datetime] = None
    total_opens: int = 0
    total_calls: int = 0
    total_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.name,
            "window": [
                {
                    "timestamp": r.timestamp.isoformat() if isinstance(r.timestamp, datetime) else str(r.timestamp),
                    "success": r.success,
                    "latency_seconds": r.latency_seconds,
                    "failure_mode": r.failure_mode,
                }
                for r in self.window
            ],
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "total_opens": self.total_opens,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BreakerSnapshot":
        window = []
        for r in d.get("window", []):
            ts = r["timestamp"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            window.append(CallRecord(
                timestamp=ts,
                success=r["success"],
                latency_seconds=r["latency_seconds"],
                failure_mode=r.get("failure_mode"),
            ))
        opened_at = d.get("opened_at")
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at)
        state = d["state"]
        if isinstance(state, str):
            state = BreakerState[state]
        return cls(
            name=d["name"],
            state=state,
            window=window,
            opened_at=opened_at,
            total_opens=d.get("total_opens", 0),
            total_calls=d.get("total_calls", 0),
            total_failures=d.get("total_failures", 0),
        )


class CircuitBreaker:
    def __init__(
        self,
        config: BreakerConfig,
        snapshot: BreakerSnapshot,
        clock: Optional[Callable] = None,
        on_state_change: Optional[Callable] = None,
    ):
        self.config = config
        self._state = snapshot.state
        self._window: list = list(snapshot.window)
        self._opened_at = snapshot.opened_at
        self._total_opens = snapshot.total_opens
        self._total_calls = snapshot.total_calls
        self._total_failures = snapshot.total_failures
        self._probe_in_flight = False
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_state_change = on_state_change

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def opened_at(self) -> Optional[datetime]:
        return self._opened_at

    @property
    def window(self) -> list:
        return list(self._window)

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def total_failures(self) -> int:
        return self._total_failures

    @property
    def total_opens(self) -> int:
        return self._total_opens

    def allow_call(self) -> bool:
        if self._state == BreakerState.CLOSED:
            return True

        if self._state == BreakerState.OPEN:
            if self._opened_at is None:
                self._transition(BreakerState.HALF_OPEN, "no_opened_at")
                self._probe_in_flight = True
                return True
            elapsed = (self._clock() - self._opened_at).total_seconds()
            if elapsed >= self.config.cooldown_seconds:
                self._transition(BreakerState.HALF_OPEN, "cooldown_elapsed")
                self._probe_in_flight = True
                return True
            return False

        if self._state == BreakerState.HALF_OPEN:
            if not self._probe_in_flight:
                self._probe_in_flight = True
                return True
            return False

        return False

    def record_success(self, latency: float) -> None:
        record = CallRecord(
            timestamp=self._clock(),
            success=True,
            latency_seconds=latency,
        )
        self._window.append(record)
        self._total_calls += 1
        self._evict_window()

        if self._state == BreakerState.HALF_OPEN:
            self._transition(BreakerState.CLOSED, "probe_success")

    def record_failure(self, latency: float, mode: str) -> None:
        record = CallRecord(
            timestamp=self._clock(),
            success=False,
            latency_seconds=latency,
            failure_mode=mode,
        )
        self._window.append(record)
        self._total_calls += 1
        self._total_failures += 1
        self._evict_window()

        if self._state == BreakerState.HALF_OPEN:
            self._transition(BreakerState.OPEN, "probe_failure")
        elif self._state == BreakerState.CLOSED:
            if self._should_open():
                self._transition(BreakerState.OPEN, "failure_threshold")

    def success_rate(self) -> float:
        if len(self._window) < self.config.min_calls_before_opening:
            return 1.0
        if not self._window:
            return 1.0
        successes = sum(1 for r in self._window if r.success)
        return successes / len(self._window)

    def to_snapshot(self) -> BreakerSnapshot:
        return BreakerSnapshot(
            name=self.config.name,
            state=self._state,
            window=list(self._window),
            opened_at=self._opened_at,
            total_opens=self._total_opens,
            total_calls=self._total_calls,
            total_failures=self._total_failures,
        )

    def _should_open(self) -> bool:
        if len(self._window) < self.config.min_calls_before_opening:
            return False
        return self.success_rate() < self.config.failure_threshold

    def _transition(self, new_state: BreakerState, reason: str) -> None:
        old_state = self._state
        self._state = new_state

        if new_state == BreakerState.OPEN:
            self._opened_at = self._clock()
            self._total_opens += 1
            self._probe_in_flight = False
        elif new_state == BreakerState.CLOSED:
            self._opened_at = None
            self._probe_in_flight = False

        if old_state != new_state and self._on_state_change:
            self._on_state_change(old_state, new_state, reason, self.success_rate())

        logger.info(
            "[breaker] %s: %s -> %s (reason=%s, success_rate=%.2f)",
            self.config.name, old_state.name, new_state.name, reason, self.success_rate(),
        )

    def _evict_window(self) -> None:
        while len(self._window) > self.config.window_size:
            self._window.pop(0)


def load_breakers(path: Path, default_config: BreakerConfig = None) -> dict:
    try:
        if not path.exists():
            return {}
        with open(path, "r") as f:
            data = json.load(f)
        breakers = {}
        for name, snap_dict in data.items():
            snap = BreakerSnapshot.from_dict(snap_dict)
            if default_config:
                cfg = BreakerConfig(
                    name=name,
                    window_size=default_config.window_size,
                    failure_threshold=default_config.failure_threshold,
                    cooldown_seconds=default_config.cooldown_seconds,
                    min_calls_before_opening=default_config.min_calls_before_opening,
                )
            else:
                cfg = BreakerConfig(name=name)
            breakers[name] = CircuitBreaker(cfg, snap)
        return breakers
    except Exception as e:
        logger.warning("[breaker] load_breakers failed (%s), returning empty", e)
        return {}


def save_breakers(breakers: dict, path: Path) -> None:
    data = {}
    for name, breaker in breakers.items():
        data[name] = breaker.to_snapshot().to_dict()
    tmp_path = str(path) + f".tmp.{os.getpid()}.{id(data)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, str(path))


def get_breaker(name: str, breakers: dict, config: BreakerConfig = None) -> CircuitBreaker:
    if name in breakers:
        return breakers[name]
    cfg = config or BreakerConfig(name=name)
    snap = BreakerSnapshot(
        name=name,
        state=BreakerState.CLOSED,
        window=[],
        opened_at=None,
    )
    breaker = CircuitBreaker(cfg, snap)
    breakers[name] = breaker
    return breaker
