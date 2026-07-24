from __future__ import annotations

from datetime import datetime, timedelta, timezone

from strategies import kalshi_optimize
from utils.kalshi import (
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
)


class RecordingObservation:
    def __init__(self) -> None:
        self.discovery = None
        self.capture = None

    def record_discovery_result(self, fields) -> None:
        self.discovery = dict(fields)

    def record_capture_summary(self, fields) -> None:
        self.capture = dict(fields)


def _market(days_to_expiry: int) -> dict:
    return {
        "id": "SYNTHETIC-EXPIRY",
        "ticker": "SYNTHETIC-EXPIRY",
        "title": "synthetic title",
        "odds": {"yes": 0.50},
        "volume_24h": 1000,
        "liquidity_usd": 1000,
        "close_time": (
            datetime.now(timezone.utc) + timedelta(days=days_to_expiry)
        ).isoformat(),
        "series_category": "financials",
        "price_history": [],
    }


def _success_result(markets: list[dict]) -> KalshiDiscoveryResult:
    count = len(markets)
    return KalshiDiscoveryResult(
        outcome=(
            DiscoveryOutcome.SUCCESS_NONEMPTY
            if count
            else DiscoveryOutcome.SUCCESS_EMPTY
        ),
        markets=tuple(markets),
        counts=DiscoveryStageCounts(
            request_attempted=1,
            page_count=2,
            raw_record_count=count,
            parsed_record_count=count,
            active_record_count=count,
            category_eligible_count=count,
            price_liquidity_eligible_count=count,
            final_eligible_count=count,
        ),
        request_status_class="2XX",
    )


def test_shadow_discovery_failure_returns_nonzero_and_never_reports_zero_success(
    monkeypatch,
) -> None:
    observation = RecordingObservation()
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.setattr(
        kalshi_optimize,
        "fetch_kalshi_markets_diagnostic",
        lambda: KalshiDiscoveryResult(
            outcome=DiscoveryOutcome.AUTH_REJECTED,
            counts=DiscoveryStageCounts(
                request_attempted=1,
                page_count=0,
            ),
            request_status_class="4XX",
        ),
    )

    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
        run_observation=observation,
        discovery_diagnostics=True,
    )

    assert result == (2, 0, 0)
    assert observation.discovery["DISCOVERY_OUTCOME"] == "AUTH_REJECTED"
    assert observation.discovery["DISCOVERY_FAILURE_PRESENT"] is True
    assert observation.discovery["DISCOVERY_FINAL_ELIGIBLE_COUNT"] == -1


def test_expiry_filtered_all_is_success_empty_with_distinct_raw_count(
    monkeypatch,
) -> None:
    observation = RecordingObservation()
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.setattr(kalshi_optimize, "MAX_DAYS_TO_EXPIRY", 3)
    monkeypatch.setattr(
        kalshi_optimize,
        "fetch_kalshi_markets_diagnostic",
        lambda: _success_result([_market(days_to_expiry=10)]),
    )
    monkeypatch.setattr(
        kalshi_optimize,
        "filter_low_liquidity_markets",
        lambda markets, **_kwargs: markets,
    )
    monkeypatch.setattr(
        kalshi_optimize,
        "generate_proof",
        lambda *_args, **_kwargs: None,
    )

    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
        run_observation=observation,
        discovery_diagnostics=True,
    )

    assert result == (0, 0, 0)
    assert observation.discovery["DISCOVERY_OUTCOME"] == "SUCCESS_EMPTY"
    assert observation.discovery["DISCOVERY_RAW_RECORD_COUNT"] == 1
    assert observation.discovery["DISCOVERY_PARSED_RECORD_COUNT"] == 1
    assert observation.discovery["DISCOVERY_EXPIRY_ELIGIBLE_COUNT"] == 0
    assert observation.discovery["DISCOVERY_FINAL_ELIGIBLE_COUNT"] == 0
    assert observation.discovery["DISCOVERY_FAILURE_PRESENT"] is False


def test_capture_disabled_success_remains_backward_compatible(monkeypatch) -> None:
    observation = RecordingObservation()
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.setattr(
        kalshi_optimize,
        "fetch_kalshi_markets_diagnostic",
        lambda: _success_result([]),
    )

    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
        run_observation=observation,
        discovery_diagnostics=True,
    )

    assert result == (0, 0, 0)
    assert observation.capture["SHADOW_CAPTURE_ENABLED"] == "false"
    assert observation.discovery["DISCOVERY_OUTCOME"] == "SUCCESS_EMPTY"


def test_non_cli_shadow_caller_retains_legacy_list_discovery(monkeypatch) -> None:
    calls = {"legacy": 0, "diagnostic": 0}

    def legacy_fetch():
        calls["legacy"] += 1
        return []

    def diagnostic_fetch():
        calls["diagnostic"] += 1
        return _success_result([])

    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.setattr(kalshi_optimize, "fetch_kalshi_markets", legacy_fetch)
    monkeypatch.setattr(
        kalshi_optimize,
        "fetch_kalshi_markets_diagnostic",
        diagnostic_fetch,
    )

    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
    )

    assert result == (0, 0, 0)
    assert calls == {"legacy": 1, "diagnostic": 0}
