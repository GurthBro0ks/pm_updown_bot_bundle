from __future__ import annotations

import subprocess
import sys

import pytest

from scripts import expanded_shadow_discovery_redacted as cli
from scripts import main_market_inventory_redacted as inventory
from utils.kalshi import (
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
)


ALLOWED_FIELDS = {
    "AUTH_CHECK",
    "REQUEST_STATUS_CLASS",
    "DISCOVERY_OUTCOME",
    "PAGE_COUNT",
    "RAW_RECORD_COUNT",
    "PARSED_RECORD_COUNT",
    "ACTIVE_RECORD_COUNT",
    "CATEGORY_ELIGIBLE_COUNT",
    "EXPIRY_ELIGIBLE_COUNT",
    "PRICE_LIQUIDITY_ELIGIBLE_COUNT",
    "FINAL_ELIGIBLE_COUNT",
    "ENDPOINT_LABEL",
}


def _result(
    outcome: DiscoveryOutcome = DiscoveryOutcome.SUCCESS_NONEMPTY,
) -> KalshiDiscoveryResult:
    return KalshiDiscoveryResult(
        outcome=outcome,
        markets=(
            (
                {
                    "ticker": "SYNTHETIC_SECRET_LOOKING_TICKER",
                    "title": "SYNTHETIC_SECRET_LOOKING_TITLE",
                    "event_name": "SYNTHETIC_SECRET_LOOKING_EVENT",
                },
            )
            if outcome is DiscoveryOutcome.SUCCESS_NONEMPTY
            else ()
        ),
        counts=DiscoveryStageCounts(
            request_attempted=1,
            page_count=2,
            raw_record_count=1,
            parsed_record_count=1,
            active_record_count=1,
            category_eligible_count=1,
            expiry_eligible_count=1,
            price_liquidity_eligible_count=1,
            final_eligible_count=(
                1 if outcome is DiscoveryOutcome.SUCCESS_NONEMPTY else 0
            ),
        ),
        request_status_class=(
            "2XX"
            if outcome in {
                DiscoveryOutcome.SUCCESS_NONEMPTY,
                DiscoveryOutcome.SUCCESS_EMPTY,
            }
            else "4XX"
        ),
    )


def test_redacted_discovery_output_has_only_fixed_approved_fields() -> None:
    output = cli.format_discovery_result(_result())
    fields = {line.split("=", 1)[0] for line in output.splitlines()}

    assert fields == ALLOWED_FIELDS
    assert cli.validate_discovery_output(output) is True
    assert "SYNTHETIC_SECRET_LOOKING" not in output
    assert "ticker" not in output.lower()
    assert "title" not in output.lower()
    assert "event" not in output.lower()
    assert "http" not in output.lower()
    assert "https://" not in output


def test_redacted_discovery_success_and_failure_exit_semantics(capsys) -> None:
    assert cli.main([], fetcher=lambda: _result(DiscoveryOutcome.SUCCESS_EMPTY)) == 0
    success_output = capsys.readouterr().out
    assert "AUTH_CHECK=PASS" in success_output
    assert "DISCOVERY_OUTCOME=SUCCESS_EMPTY" in success_output

    assert cli.main([], fetcher=lambda: _result(DiscoveryOutcome.AUTH_REJECTED)) == 1
    failure_output = capsys.readouterr().out
    assert "AUTH_CHECK=FAIL" in failure_output
    assert "DISCOVERY_OUTCOME=AUTH_REJECTED" in failure_output


def test_invalid_bounded_labels_fail_closed_without_echo() -> None:
    unsafe = KalshiDiscoveryResult(
        outcome=DiscoveryOutcome.SUCCESS_EMPTY,
        counts=DiscoveryStageCounts(),
        request_status_class="SYNTHETIC_UNBOUNDED_VALUE",
        endpoint_label="SYNTHETIC_UNBOUNDED_ENDPOINT",
    )
    output = cli.format_discovery_result(unsafe)

    assert "SYNTHETIC_UNBOUNDED" not in output
    assert "REQUEST_STATUS_CLASS=NETWORK_ERROR" in output
    assert "ENDPOINT_LABEL=SERIES_AND_MARKETS" in output


def test_output_validator_rejects_unknown_extra_or_malformed_fields() -> None:
    output = cli.format_discovery_result(_result())

    assert cli.validate_discovery_output(output + "UNKNOWN_FIELD=value\n") is False
    assert cli.validate_discovery_output(output.replace("PAGE_COUNT=2", "PAGE_COUNT=two")) is False
    assert cli.validate_discovery_output(output.replace("AUTH_CHECK=PASS", "AUTH_CHECK=MAYBE")) is False


def test_unexpected_argument_stops_before_fetch() -> None:
    called = False

    def fetcher():
        nonlocal called
        called = True
        return _result()

    assert cli.main(["--unexpected"], fetcher=fetcher) == 2
    assert called is False


@pytest.mark.parametrize(
    "outcome",
    [
        DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
        DiscoveryOutcome.AUTH_REJECTED,
        DiscoveryOutcome.NETWORK_TIMEOUT,
        DiscoveryOutcome.NETWORK_ERROR,
        DiscoveryOutcome.HTTP_ERROR,
        DiscoveryOutcome.JSON_PARSE_ERROR,
        DiscoveryOutcome.SCHEMA_ERROR,
        DiscoveryOutcome.PAGINATION_ERROR,
        DiscoveryOutcome.INTERNAL_DISCOVERY_ERROR,
    ],
)
def test_every_failure_uses_same_bounded_redacted_shape(
    outcome: DiscoveryOutcome,
) -> None:
    output = cli.format_discovery_result(_result(outcome))
    assert len(output.encode()) < 2048
    assert {line.split("=", 1)[0] for line in output.splitlines()} == ALLOWED_FIELDS
    assert "SYNTHETIC_SECRET_LOOKING" not in output
    assert f"DISCOVERY_OUTCOME={outcome.value}" in output


def test_main_inventory_no_longer_emits_ticker_samples() -> None:
    result = inventory.build_inventory(
        [
            {
                "ticker": "SYNTHETIC_SECRET_LOOKING_TICKER",
                "id": "SYNTHETIC_SECRET_LOOKING_TICKER",
                "series_category": "financials",
                "close_time": "2026-07-25T12:00:00+00:00",
            }
        ],
        max_days=3,
        allowed_categories={"financials"},
    )
    output = inventory.format_inventory(result)

    assert "SAMPLE_ALLOWED_TICKERS_PUBLIC" not in output
    assert "SAMPLE_DISALLOWED_TICKERS_PUBLIC" not in output
    assert "SYNTHETIC_SECRET_LOOKING_TICKER" not in output


def test_direct_invocation_help_is_not_required_and_import_is_side_effect_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import scripts.expanded_shadow_discovery_redacted as module;"
                "print(module.__name__)"
            ),
        ],
        cwd="/opt/slimy/pm_updown_bot_bundle",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "scripts.expanded_shadow_discovery_redacted"
