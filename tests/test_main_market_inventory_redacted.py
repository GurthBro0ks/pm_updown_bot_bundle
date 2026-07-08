import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

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
