#!/usr/bin/env python3
"""Redacted read-only inventory for main Kalshi market candidates.

The command prints only public market metadata summaries: counts, public
categories, and public tickers. It does not place, cancel, or modify orders.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Callable, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Match existing redacted health-check behavior: config loads runtime env for
# the read-only Kalshi client path. This script never prints loaded values.
import config as _runtime_config  # noqa: F401
from utils.kalshi import fetch_kalshi_markets


DEFAULT_MAX_DAYS = 3
DEFAULT_ALLOWED_CATEGORIES = (
    "index",
    "crypto",
    "economics",
    "commodities",
    "financials",
)

TICKER_CATEGORY_MAP = {
    "KXINX": "index",
    "KXINXU": "index",
    "KXNDX": "index",
    "KXNASDAQ100": "index",
    "KXNASDAQ100U": "index",
    "KXBTC": "crypto",
    "KXETH": "crypto",
    "KXETHY": "crypto",
    "KXECON": "economics",
    "KXNATGAS": "commodities",
    "KXOIL": "commodities",
    "KXFED": "financials",
    "KXRATE": "financials",
    "KXMVESPORTS": "sports",
    "KXBUNDESLIGA": "sports",
    "KXCOACH": "sports",
    "KXNFL": "sports",
}

WEATHER_PREFIXES = (
    "KXHIGH",
    "KXHIGHT",
    "KXRAIN",
    "KXTEMP",
    "KXSNOW",
    "KXWIND",
)

PUBLIC_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


@dataclass(frozen=True)
class InventoryResult:
    status: str
    max_days: int
    total_fetched: int
    after_expiry_filter: int
    after_weather_exclusion: int
    after_category_filter: int
    category_counts: dict[str, int]
    three_day_allowed_count: int
    three_day_disallowed_count: int
    zero_candidate_reason: str
    sample_allowed_tickers: list[str]
    sample_disallowed_tickers: list[str]
    supplemental_series_market_count: int
    supplemental_three_day_allowed_count: int


def parse_allowed_categories(raw: str | Sequence[str]) -> set[str]:
    if isinstance(raw, str):
        items = raw.split(",")
    else:
        items = raw
    return {str(item).strip().lower() for item in items if str(item).strip()}


def extract_market_category(market: dict) -> str:
    series_category = market.get("series_category") or market.get("category")
    if series_category:
        category = str(series_category).strip().lower()
        if category in {
            "index",
            "crypto",
            "economics",
            "commodities",
            "financials",
            "politics",
            "sports",
            "esports",
            "entertainment",
            "social",
            "weather",
            "climate",
        }:
            return "weather" if category in {"weather", "climate"} else category
        if category in {"other", "unknown", ""}:
            return "other"

    ticker = str(market.get("ticker") or market.get("id") or "").upper()
    prefix = ticker.split("-")[0] if "-" in ticker else ticker[:12]
    for key, category in TICKER_CATEGORY_MAP.items():
        if prefix.startswith(key):
            return category
    if is_weather_market(market):
        return "weather"
    if any(token in ticker for token in ("SPORTS", "NFL", "NBA", "MLB", "NHL")):
        return "sports"
    if "INX" in ticker or "NASDAQ" in ticker:
        return "index"
    if "BTC" in ticker or "ETH" in ticker:
        return "crypto"
    return "other"


def is_weather_market(market: dict) -> bool:
    ticker = str(market.get("ticker") or market.get("id") or "").upper()
    series = str(market.get("series_ticker") or "").upper()
    category = str(market.get("series_category") or market.get("category") or "").lower()
    return (
        ticker.startswith(WEATHER_PREFIXES)
        or series.startswith(WEATHER_PREFIXES)
        or category in {"weather", "climate"}
    )


def days_to_expiry(market: dict, now: datetime) -> float | None:
    end_time = market.get("close_time") or market.get("expiration_date")
    if not end_time:
        return None
    try:
        if isinstance(end_time, str):
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        else:
            end_dt = end_time
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        return (end_dt.timestamp() - now.timestamp()) / 86400
    except Exception:
        return None


def public_token(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return "none"
    return PUBLIC_TOKEN_RE.sub("_", token)[:120]


def sample_tickers(markets: Iterable[dict], sample_limit: int) -> list[str]:
    tickers = []
    for market in markets:
        ticker = public_token(market.get("ticker") or market.get("id"))
        if ticker != "none":
            tickers.append(ticker)
        if len(tickers) >= sample_limit:
            break
    return tickers


def classify_zero_candidate_reason(
    *,
    total_fetched: int,
    after_expiry_filter: int,
    after_weather_exclusion: int,
    after_category_filter: int,
    three_day_disallowed_count: int,
) -> str:
    if after_category_filter > 0:
        return "has_allowed_candidates"
    if total_fetched == 0:
        return "no_markets_fetched"
    if after_expiry_filter == 0:
        return "no_current_3_day_markets"
    if after_weather_exclusion == 0:
        return "all_3_day_markets_weather"
    if three_day_disallowed_count > 0:
        return "no_3_day_allowed_category_markets"
    return "unknown_need_redacted_main_status_tool"


def build_inventory(
    markets: Sequence[dict],
    *,
    max_days: int = DEFAULT_MAX_DAYS,
    allowed_categories: set[str] | None = None,
    sample_limit: int = 20,
    now: datetime | None = None,
) -> InventoryResult:
    allowed = allowed_categories or set(DEFAULT_ALLOWED_CATEGORIES)
    now = now or datetime.now(timezone.utc)

    expiry_filtered = [
        market for market in markets
        if (days_left := days_to_expiry(market, now)) is not None
        and days_left <= max_days
    ]
    non_weather = [market for market in expiry_filtered if not is_weather_market(market)]
    categories = {id(market): extract_market_category(market) for market in non_weather}
    category_counts = dict(sorted(Counter(categories.values()).items()))
    allowed_markets = [
        market for market in non_weather
        if categories[id(market)] in allowed
    ]
    disallowed_markets = [
        market for market in non_weather
        if categories[id(market)] not in allowed
    ]
    reason = classify_zero_candidate_reason(
        total_fetched=len(markets),
        after_expiry_filter=len(expiry_filtered),
        after_weather_exclusion=len(non_weather),
        after_category_filter=len(allowed_markets),
        three_day_disallowed_count=len(disallowed_markets),
    )

    return InventoryResult(
        status="PASS",
        max_days=max_days,
        total_fetched=len(markets),
        after_expiry_filter=len(expiry_filtered),
        after_weather_exclusion=len(non_weather),
        after_category_filter=len(allowed_markets),
        category_counts=category_counts,
        three_day_allowed_count=len(allowed_markets),
        three_day_disallowed_count=len(disallowed_markets),
        zero_candidate_reason=reason,
        sample_allowed_tickers=sample_tickers(allowed_markets, sample_limit),
        sample_disallowed_tickers=sample_tickers(disallowed_markets, sample_limit),
        supplemental_series_market_count=sum(
            1 for market in markets
            if market.get("kalshi_fetch_source") == "supplemental_series"
        ),
        supplemental_three_day_allowed_count=sum(
            1 for market in allowed_markets
            if market.get("kalshi_fetch_source") == "supplemental_series"
        ),
    )


def format_inventory(result: InventoryResult) -> str:
    category_counts = ",".join(
        f"{public_token(category)}:{count}"
        for category, count in result.category_counts.items()
    ) or "none"
    allowed_sample = ",".join(result.sample_allowed_tickers) or "none"
    disallowed_sample = ",".join(result.sample_disallowed_tickers) or "none"
    lines = [
        f"MARKET_INVENTORY={result.status}",
        "VALUES_PRINTED=no_secret_values",
        f"MAX_DAYS={result.max_days}",
        f"TOTAL_FETCHED={result.total_fetched}",
        f"AFTER_EXPIRY_FILTER={result.after_expiry_filter}",
        f"AFTER_WEATHER_EXCLUSION={result.after_weather_exclusion}",
        f"AFTER_CATEGORY_FILTER={result.after_category_filter}",
        f"CATEGORY_COUNTS_PUBLIC={category_counts}",
        f"THREE_DAY_ALLOWED_COUNT={result.three_day_allowed_count}",
        f"THREE_DAY_DISALLOWED_COUNT={result.three_day_disallowed_count}",
        f"SUPPLEMENTAL_SERIES_MARKET_COUNT={result.supplemental_series_market_count}",
        f"SUPPLEMENTAL_THREE_DAY_ALLOWED_COUNT={result.supplemental_three_day_allowed_count}",
        f"ZERO_CANDIDATE_REASON={result.zero_candidate_reason}",
        f"SAMPLE_ALLOWED_TICKERS_PUBLIC={allowed_sample}",
        f"SAMPLE_DISALLOWED_TICKERS_PUBLIC={disallowed_sample}",
    ]
    return "\n".join(lines) + "\n"


def run_inventory(
    *,
    max_days: int,
    allowed_categories: set[str],
    sample_limit: int,
    fetcher: Callable[[], Sequence[dict]] = fetch_kalshi_markets,
) -> InventoryResult:
    markets = list(fetcher())
    return build_inventory(
        markets,
        max_days=max_days,
        allowed_categories=allowed_categories,
        sample_limit=sample_limit,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print redacted read-only main-market inventory counts."
    )
    parser.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    parser.add_argument(
        "--allowed-categories",
        default=",".join(DEFAULT_ALLOWED_CATEGORIES),
        help="Comma-separated category allowlist for main bot inventory.",
    )
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_days != DEFAULT_MAX_DAYS:
        print("MARKET_INVENTORY=FAIL")
        print("VALUES_PRINTED=no_secret_values")
        print(f"MAX_DAYS={args.max_days}")
        print("ZERO_CANDIDATE_REASON=max_days_must_remain_3")
        return 2
    allowed_categories = parse_allowed_categories(args.allowed_categories)
    result = run_inventory(
        max_days=args.max_days,
        allowed_categories=allowed_categories,
        sample_limit=max(0, args.sample_limit),
    )
    print(format_inventory(result), end="")
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
