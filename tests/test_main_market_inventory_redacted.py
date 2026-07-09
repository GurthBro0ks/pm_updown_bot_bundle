import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

from scripts import main_market_inventory_redacted as inventory


NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
ALLOWED = {"index", "crypto", "economics", "commodities", "financials"}


def _market(ticker, days, category, **extra):
    market = {
        "ticker": ticker,
        "id": ticker,
        "series_category": category,
        "close_time": (NOW + timedelta(days=days)).isoformat(),
        "question": extra.pop("question", "public title"),
    }
    market.update(extra)
    return market


def test_three_day_allowed_category_survives():
    result = inventory.build_inventory(
        [_market("KXINXU-26JUL08H1200-T7000", 1, "index")],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.after_expiry_filter == 1
    assert result.after_weather_exclusion == 1
    assert result.after_category_filter == 1
    assert result.three_day_allowed_count == 1
    assert result.zero_candidate_reason == "has_allowed_candidates"


def test_over_three_day_market_is_excluded():
    result = inventory.build_inventory(
        [_market("KXBTCY-27JAN0100-B92500", 177, "crypto")],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.total_fetched == 1
    assert result.after_expiry_filter == 0
    assert result.after_category_filter == 0
    assert result.zero_candidate_reason == "no_current_3_day_markets"


def test_weather_market_is_excluded_from_main_inventory():
    result = inventory.build_inventory(
        [_market("KXHIGHNY-26JUL08-B85", 1, "weather")],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.after_expiry_filter == 1
    assert result.after_weather_exclusion == 0
    assert result.after_category_filter == 0
    assert result.zero_candidate_reason == "all_3_day_markets_weather"


def test_disallowed_category_is_counted_but_not_allowed():
    result = inventory.build_inventory(
        [_market("KXNFLGAME-26JUL08-TEAM", 1, "sports")],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.category_counts == {"sports": 1}
    assert result.three_day_allowed_count == 0
    assert result.three_day_disallowed_count == 1
    assert result.zero_candidate_reason == "no_3_day_allowed_category_markets"


def test_supplemental_series_allowed_short_horizon_is_reported():
    result = inventory.build_inventory(
        [
            _market(
                "KXNASDAQ100U-26JUL08H1600-T29199.99",
                0.25,
                "financials",
                kalshi_fetch_source="supplemental_series",
            )
        ],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.max_days == 3
    assert result.after_category_filter == 1
    assert result.three_day_allowed_count == 1
    assert result.supplemental_series_market_count == 1
    assert result.supplemental_three_day_allowed_count == 1
    assert result.zero_candidate_reason == "has_allowed_candidates"


def test_supplemental_series_still_goes_through_expiry_filter():
    result = inventory.build_inventory(
        [
            _market(
                "KXNASDAQ100U-26JUL20H1600-T29199.99",
                12,
                "financials",
                kalshi_fetch_source="supplemental_series",
            )
        ],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.supplemental_series_market_count == 1
    assert result.supplemental_three_day_allowed_count == 0
    assert result.zero_candidate_reason == "no_current_3_day_markets"


def test_supplemental_series_weather_market_is_excluded():
    result = inventory.build_inventory(
        [
            _market(
                "KXHIGHNY-26JUL08-B85",
                1,
                "weather",
                kalshi_fetch_source="supplemental_series",
            )
        ],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.supplemental_series_market_count == 1
    assert result.supplemental_three_day_allowed_count == 0
    assert result.zero_candidate_reason == "all_3_day_markets_weather"


def test_supplemental_series_disallowed_category_is_excluded():
    result = inventory.build_inventory(
        [
            _market(
                "KXNFLGAME-26JUL08-TEAM",
                1,
                "sports",
                kalshi_fetch_source="supplemental_series",
            )
        ],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.supplemental_series_market_count == 1
    assert result.supplemental_three_day_allowed_count == 0
    assert result.category_counts == {"sports": 1}
    assert result.zero_candidate_reason == "no_3_day_allowed_category_markets"


def test_inventory_fetcher_only_fetches_markets_without_order_action():
    calls = {"fetch": 0, "order": 0}

    def fake_fetcher():
        calls["fetch"] += 1
        return [
            _market(
                "KXNASDAQ100U-26JUL08H1600-T29199.99",
                1,
                "financials",
                kalshi_fetch_source="supplemental_series",
            )
        ]

    result = inventory.run_inventory(
        max_days=3,
        allowed_categories=ALLOWED,
        sample_limit=20,
        fetcher=fake_fetcher,
    )

    assert calls == {"fetch": 1, "order": 0}
    assert result.three_day_allowed_count == 1


def test_inventory_fetch_requests_identity_encoding_for_decode_robustness():
    seen_headers = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {}

    def fake_request_get(_url, **kwargs):
        seen_headers.update(kwargs.get("headers") or {})
        return FakeResponse()

    def fake_fetcher():
        inventory.kalshi_utils.requests.get("https://example.test/markets", headers={"X-Test": "1"})
        return [
            _market(
                "KXNASDAQ100U-26JUL08H1600-T29199.99",
                1,
                "financials",
                kalshi_fetch_source="supplemental_series",
            )
        ]

    result = inventory.run_inventory(
        max_days=3,
        allowed_categories=ALLOWED,
        sample_limit=20,
        fetcher=fake_fetcher,
        request_get=fake_request_get,
    )

    assert result.status == "PASS"
    assert seen_headers["Accept-Encoding"] == "identity"
    assert seen_headers["X-Test"] == "1"
    assert result.three_day_allowed_count == 1


def test_inventory_decode_failure_is_warn_not_misleading_zero_fetch():
    def fake_request_get(_url, **_kwargs):
        raise requests.exceptions.ContentDecodingError("brotli content-encoding decode failed")

    def fake_fetcher():
        try:
            inventory.kalshi_utils.requests.get("https://example.test/series")
        except Exception:
            return []
        return []

    result = inventory.run_inventory(
        max_days=3,
        allowed_categories=ALLOWED,
        sample_limit=20,
        fetcher=fake_fetcher,
        request_get=fake_request_get,
    )
    output = inventory.format_inventory(result)

    assert result.status == "WARN_DECODE_UNSUPPORTED"
    assert result.zero_candidate_reason == "decode_unsupported_fail_closed"
    assert "MARKET_INVENTORY=WARN_DECODE_UNSUPPORTED" in output
    assert "ZERO_CANDIDATE_REASON=decode_unsupported_fail_closed" in output
    assert "brotli" not in output


def test_zero_candidate_reason_prefers_no_current_three_day_markets():
    result = inventory.build_inventory(
        [
            _market("KXETHY-27JAN0100-B2875", 177, "crypto"),
            _market("KXBTCY-27JAN0100-B92500", 177, "crypto"),
        ],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )

    assert result.zero_candidate_reason == "no_current_3_day_markets"


def test_output_does_not_include_fake_secret_looking_values():
    result = inventory.build_inventory(
        [
            _market(
                "KXNFLGAME-26JUL08-TEAM",
                1,
                "sports",
                question="SECRET_TOKEN=fake-private-value Bearer fake-auth",
            )
        ],
        max_days=3,
        allowed_categories=ALLOWED,
        now=NOW,
    )
    output = inventory.format_inventory(result)

    assert "SECRET_TOKEN" not in output
    assert "fake-private-value" not in output
    assert "Bearer" not in output
    assert "VALUES_PRINTED=no_secret_values" in output
    assert "SUPPLEMENTAL_SERIES_MARKET_COUNT" in output
    assert "SUPPLEMENTAL_THREE_DAY_ALLOWED_COUNT" in output


def test_direct_invocation_help_works_without_pythonpath():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "scripts/main_market_inventory_redacted.py", "--help"],
        cwd="/opt/slimy/pm_updown_bot_bundle",
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "main-market inventory" in completed.stdout


def test_cli_rejects_widened_max_days(capsys):
    exit_code = inventory.main(["--max-days", "14"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "MARKET_INVENTORY=FAIL" in output
    assert "MAX_DAYS=14" in output
    assert "max_days_must_remain_3" in output
