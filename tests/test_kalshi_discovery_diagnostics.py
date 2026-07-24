from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests

from utils import kalshi


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, error: Exception | None = None):
        self.status_code = status_code
        self._payload = payload
        self._error = error

    def json(self):
        if self._error is not None:
            raise self._error
        return self._payload


def _request_sequence(*items):
    remaining = list(items)

    def request_get(_url, **_kwargs):
        if not remaining:
            raise AssertionError("unexpected synthetic request")
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return request_get


def _series(ticker: str = "KXTEST", category: str = "Financials") -> dict:
    return {
        "ticker": ticker,
        "category": category,
        "volume": 10,
        "fee_multiplier": 0.07,
    }


def _market(ticker: str = "KXTEST-SYNTHETIC", **overrides) -> dict:
    market = {
        "ticker": ticker,
        "short_name": "synthetic title never emitted",
        "status": "active",
        "yes_bid_dollars": "0.49",
        "yes_ask_dollars": "0.50",
        "volume_24h_fp": "10",
        "open_interest_fp": "10",
        "close_time": "2026-07-25T12:00:00Z",
    }
    market.update(overrides)
    return market


def _discover(
    *responses,
    environment: dict[str, str] | None = None,
    normalizer=kalshi.normalize_kalshi_market,
):
    values = {"KALSHI_KEY": "synthetic-key"}
    values.update(environment or {})
    return kalshi.fetch_kalshi_markets_diagnostic(
        environment=values,
        private_key=object(),
        request_get=_request_sequence(*responses),
        header_factory=lambda *_args: {},
        normalizer=normalizer,
    )


def test_successful_empty_response_is_not_failure() -> None:
    result = _discover(FakeResponse(payload={"series": []}))

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_EMPTY
    assert result.failure_present is False
    assert result.market_list() == []
    assert result.counts.request_attempted == 1
    assert result.counts.page_count == 1
    assert result.counts.raw_record_count == 0
    assert result.counts.parsed_record_count == 0
    assert result.counts.final_eligible_count == 0


def test_successful_one_page_nonempty_response_has_stage_counts() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [_market()]}),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_NONEMPTY
    assert len(result.market_list()) == 1
    assert result.counts.page_count == 2
    assert result.counts.raw_record_count == 1
    assert result.counts.parsed_record_count == 1
    assert result.counts.active_record_count == 1
    assert result.counts.category_eligible_count == 1
    assert result.counts.price_liquidity_eligible_count == 1
    assert result.counts.final_eligible_count == 1


def test_successful_multi_page_response_fetches_every_cursor() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series("KXONE")], "cursor": "next-series"}),
        FakeResponse(payload={"series": [_series("KXTWO")]}),
        FakeResponse(payload={"markets": [_market("KXONE-SYNTHETIC")]}),
        FakeResponse(payload={"markets": [_market("KXTWO-SYNTHETIC")]}),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_NONEMPTY
    assert result.counts.page_count == 4
    assert result.counts.raw_record_count == 2
    assert result.counts.final_eligible_count == 2


def test_missing_credential_configuration_is_explicit_without_request() -> None:
    result = kalshi.fetch_kalshi_markets_diagnostic(environment={})

    assert result.outcome is kalshi.DiscoveryOutcome.AUTH_CONFIGURATION_MISSING
    assert result.failure_present is True
    assert result.counts.request_attempted == 0
    assert result.request_status_class == "NOT_ATTEMPTED"


@pytest.mark.parametrize(
    ("status_code", "outcome"),
    [
        (401, kalshi.DiscoveryOutcome.AUTH_REJECTED),
        (403, kalshi.DiscoveryOutcome.AUTH_REJECTED),
        (429, kalshi.DiscoveryOutcome.HTTP_ERROR),
        (500, kalshi.DiscoveryOutcome.HTTP_ERROR),
    ],
)
def test_http_failures_are_explicit(status_code, outcome) -> None:
    result = _discover(FakeResponse(status_code=status_code, payload={}))

    assert result.outcome is outcome
    assert result.failure_present is True
    assert result.request_status_class == ("4XX" if status_code < 500 else "5XX")
    assert result.market_list() == []


def test_network_timeout_is_explicit() -> None:
    result = _discover(requests.exceptions.Timeout("synthetic timeout"))

    assert result.outcome is kalshi.DiscoveryOutcome.NETWORK_TIMEOUT
    assert result.request_status_class == "NETWORK_ERROR"


def test_connection_error_is_explicit() -> None:
    result = _discover(requests.exceptions.ConnectionError("synthetic connection"))

    assert result.outcome is kalshi.DiscoveryOutcome.NETWORK_ERROR
    assert result.request_status_class == "NETWORK_ERROR"


def test_malformed_json_is_explicit() -> None:
    result = _discover(FakeResponse(error=ValueError("synthetic malformed json")))

    assert result.outcome is kalshi.DiscoveryOutcome.JSON_PARSE_ERROR
    assert result.counts.page_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"series": "not-a-list"},
        {"series": ["not-a-record"]},
        {"series": [{"category": "Financials"}]},
    ],
)
def test_series_schema_mismatch_is_explicit(payload) -> None:
    result = _discover(FakeResponse(payload=payload))

    assert result.outcome is kalshi.DiscoveryOutcome.SCHEMA_ERROR
    assert result.failure_present is True


def test_market_record_schema_error_is_explicit() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [{"status": "active"}]}),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SCHEMA_ERROR
    assert result.counts.raw_record_count == 1
    assert result.counts.parsed_record_count is None


def test_parser_rejecting_every_record_is_not_success_empty() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [_market()]}),
        normalizer=lambda _market: {},
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SCHEMA_ERROR
    assert result.failure_present is True
    assert result.counts.raw_record_count == 1
    assert result.counts.parsed_record_count is None


def test_parser_exception_is_schema_failure_without_exception_text() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [_market()]}),
        normalizer=lambda _market: (_ for _ in ()).throw(
            RuntimeError("SYNTHETIC_SECRET_LOOKING_EXCEPTION")
        ),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SCHEMA_ERROR
    assert "SYNTHETIC_SECRET_LOOKING" not in repr(result.status_fields())


def test_unexpected_internal_failure_is_closed_and_redacted() -> None:
    result = _discover(RuntimeError("SYNTHETIC_SECRET_LOOKING_INTERNAL"))

    assert result.outcome is kalshi.DiscoveryOutcome.INTERNAL_DISCOVERY_ERROR
    assert result.failure_present is True
    assert "SYNTHETIC_SECRET_LOOKING" not in repr(result.status_fields())


def test_all_records_closed_is_success_empty_after_active_stage() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [_market(status="closed")]}),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_EMPTY
    assert result.counts.raw_record_count == 1
    assert result.counts.parsed_record_count == 1
    assert result.counts.active_record_count == 0
    assert result.counts.final_eligible_count == 0


def test_all_records_wrong_category_is_filter_empty_success() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series(category="Sports")]}),
        FakeResponse(payload={"markets": [_market()]}),
        environment={"KALSHI_FETCH_INCLUDE_CATEGORIES": "financials"},
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_EMPTY
    assert result.counts.raw_record_count == 1
    assert result.counts.active_record_count == 1
    assert result.counts.category_eligible_count == 0


def test_all_records_fail_price_liquidity_is_filter_empty_success() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(
            payload={
                "markets": [
                    _market(
                        yes_bid_dollars="",
                        yes_ask_dollars="",
                        last_price_dollars="",
                    )
                ]
            }
        ),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_EMPTY
    assert result.counts.active_record_count == 1
    assert result.counts.price_liquidity_eligible_count == 0
    assert result.counts.final_eligible_count == 0


def test_later_market_page_contains_only_eligible_record() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [], "cursor": "next-market"}),
        FakeResponse(payload={"markets": [_market()]}),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_NONEMPTY
    assert result.counts.page_count == 3
    assert result.counts.raw_record_count == 1
    assert result.counts.final_eligible_count == 1


def test_later_page_failure_discards_partial_records_and_is_pagination_error() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [_market()], "cursor": "next-market"}),
        FakeResponse(status_code=500, payload={}),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.PAGINATION_ERROR
    assert result.failure_present is True
    assert result.market_list() == []
    assert result.counts.page_count == 2
    assert result.counts.raw_record_count == 1


def test_repeated_cursor_is_pagination_error() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()], "cursor": "repeat"}),
        FakeResponse(payload={"series": [], "cursor": "repeat"}),
    )

    assert result.outcome is kalshi.DiscoveryOutcome.PAGINATION_ERROR


def test_status_fields_are_bounded_and_contain_no_record_values() -> None:
    secret_ticker = "SYNTHETIC_SECRET_LOOKING_TICKER"
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(
            payload={
                "markets": [
                    _market(
                        secret_ticker,
                        short_name="SYNTHETIC_SECRET_LOOKING_TITLE",
                    )
                ]
            }
        ),
    )

    fields = result.status_fields()
    rendered = repr(fields)
    assert fields["DISCOVERY_OUTCOME"] == "SUCCESS_NONEMPTY"
    assert fields["DISCOVERY_FAILURE_PRESENT"] is False
    assert all(
        isinstance(value, (int, bool, str))
        for value in fields.values()
    )
    assert "SYNTHETIC_SECRET_LOOKING" not in rendered


def test_strategy_count_adapter_distinguishes_expiry_and_final_empty() -> None:
    result = _discover(
        FakeResponse(payload={"series": [_series()]}),
        FakeResponse(payload={"markets": [_market()]}),
    ).with_strategy_counts(
        expiry_eligible_count=0,
        category_eligible_count=0,
        final_eligible_count=0,
    )

    assert result.outcome is kalshi.DiscoveryOutcome.SUCCESS_EMPTY
    assert result.counts.raw_record_count == 1
    assert result.counts.parsed_record_count == 1
    assert result.counts.expiry_eligible_count == 0
    assert result.counts.final_eligible_count == 0
