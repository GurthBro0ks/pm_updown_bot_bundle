"""
Kalshi API utilities - Fixed Series-Based Market Discovery
Uses Series API to filter out sports/esports and target financial markets
"""

import os
import requests
import time
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
import base64
from typing import Callable, Mapping

from utils.kalshi_normalize import normalize_kalshi_market

logger = logging.getLogger(__name__)

# REMOVED 2026-03-14: KALSHI_TARGET_CATEGORIES - legacy allowlist, now using blocklist-only filtering
# Previously defined categories: Economics, Politics, Financials, Elections, Companies,
# Climate and Weather, World, Crypto, Science and Technology

# Explicit blocklist — never trade these
KALSHI_BLOCKED_CATEGORIES = {
    "Entertainment",    # 2138 series - mostly celebrity/TV
    "Mentions",        # 271 series - social media mentions
    "Social",          # 75 series - social events
    "Exotics",         # 8 series - weird exotic markets
    # REMOVED 2026-03-14: Sports, Esports - needed for current active markets
}

# REMOVED 2026-03-14: All prefixes removed to allow esports/sports markets
# Previously blocked: KXMV, KXMVESPORTS, KXNFL, KXNBA, KXMLB, KXNHL, KXEPL, KXUCL
KALSHI_BLOCKED_PREFIXES = ()
KALSHI_CANONICAL_BASE_URL = "https://api.elections.kalshi.com"
KALSHI_MAIN_SUPPLEMENTAL_SERIES = ("KXNASDAQ100U",)
MAX_DISCOVERY_PAGES = 100


class DiscoveryOutcome(str, Enum):
    """Closed, redacted result taxonomy for diagnostic market discovery."""

    SUCCESS_NONEMPTY = "SUCCESS_NONEMPTY"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    AUTH_CONFIGURATION_MISSING = "AUTH_CONFIGURATION_MISSING"
    AUTH_REJECTED = "AUTH_REJECTED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    JSON_PARSE_ERROR = "JSON_PARSE_ERROR"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    PAGINATION_ERROR = "PAGINATION_ERROR"
    INTERNAL_DISCOVERY_ERROR = "INTERNAL_DISCOVERY_ERROR"


DISCOVERY_FAILURE_OUTCOMES = frozenset(
    outcome
    for outcome in DiscoveryOutcome
    if outcome not in {
        DiscoveryOutcome.SUCCESS_NONEMPTY,
        DiscoveryOutcome.SUCCESS_EMPTY,
    }
)

REQUEST_STATUS_CLASSES = frozenset(
    {"NOT_ATTEMPTED", "2XX", "4XX", "5XX", "NETWORK_ERROR"}
)
DISCOVERY_ENDPOINT_LABELS = frozenset({"SERIES_AND_MARKETS"})


@dataclass(frozen=True)
class DiscoveryStageCounts:
    """Nonnegative counts; ``None`` means the stage was not reached."""

    request_attempted: int = 0
    page_count: int = 0
    raw_record_count: int | None = None
    parsed_record_count: int | None = None
    active_record_count: int | None = None
    category_eligible_count: int | None = None
    expiry_eligible_count: int | None = None
    price_liquidity_eligible_count: int | None = None
    final_eligible_count: int | None = None


@dataclass(frozen=True)
class KalshiDiscoveryResult:
    """Structured discovery result with an explicit list compatibility adapter."""

    outcome: DiscoveryOutcome
    markets: tuple[dict, ...] = ()
    counts: DiscoveryStageCounts = DiscoveryStageCounts()
    request_status_class: str = "NOT_ATTEMPTED"
    endpoint_label: str = "SERIES_AND_MARKETS"

    @property
    def failure_present(self) -> bool:
        return self.outcome in DISCOVERY_FAILURE_OUTCOMES

    def market_list(self) -> list[dict]:
        """Return the legacy mutable list shape without exposing diagnostics."""

        return list(self.markets)

    def with_strategy_counts(
        self,
        *,
        expiry_eligible_count: int,
        category_eligible_count: int,
        final_eligible_count: int,
    ) -> "KalshiDiscoveryResult":
        """Attach scanner-stage counts without changing the discovered records."""

        if self.failure_present:
            return self
        final_count = max(0, int(final_eligible_count))
        return replace(
            self,
            outcome=(
                DiscoveryOutcome.SUCCESS_NONEMPTY
                if final_count
                else DiscoveryOutcome.SUCCESS_EMPTY
            ),
            counts=replace(
                self.counts,
                expiry_eligible_count=max(0, int(expiry_eligible_count)),
                category_eligible_count=max(0, int(category_eligible_count)),
                final_eligible_count=final_count,
            ),
        )

    def status_fields(self) -> dict[str, object]:
        """Return only bounded enums, booleans, and integer telemetry."""

        def count(value: int | None) -> int:
            return -1 if value is None else max(0, int(value))

        return {
            "DISCOVERY_OUTCOME": self.outcome.value,
            "DISCOVERY_REQUEST_ATTEMPTED": int(bool(self.counts.request_attempted)),
            "DISCOVERY_PAGE_COUNT": count(self.counts.page_count),
            "DISCOVERY_RAW_RECORD_COUNT": count(self.counts.raw_record_count),
            "DISCOVERY_PARSED_RECORD_COUNT": count(self.counts.parsed_record_count),
            "DISCOVERY_ACTIVE_RECORD_COUNT": count(self.counts.active_record_count),
            "DISCOVERY_CATEGORY_ELIGIBLE_COUNT": count(
                self.counts.category_eligible_count
            ),
            "DISCOVERY_EXPIRY_ELIGIBLE_COUNT": count(
                self.counts.expiry_eligible_count
            ),
            "DISCOVERY_PRICE_LIQUIDITY_ELIGIBLE_COUNT": count(
                self.counts.price_liquidity_eligible_count
            ),
            "DISCOVERY_FINAL_ELIGIBLE_COUNT": count(
                self.counts.final_eligible_count
            ),
            "DISCOVERY_FAILURE_PRESENT": self.failure_present,
        }


class _DiscoveryFailure(RuntimeError):
    """Internal control flow carrying only the bounded failure taxonomy."""

    def __init__(
        self,
        outcome: DiscoveryOutcome,
        request_status_class: str,
    ) -> None:
        super().__init__(outcome.value)
        self.outcome = outcome
        self.request_status_class = request_status_class


@dataclass
class _DiscoveryProgress:
    request_attempted: int = 0
    page_count: int = 0
    raw_record_count: int | None = None
    parsed_record_count: int | None = None
    active_record_count: int | None = None
    category_eligible_count: int | None = None
    price_liquidity_eligible_count: int | None = None
    final_eligible_count: int | None = None

    def counts(self) -> DiscoveryStageCounts:
        return DiscoveryStageCounts(
            request_attempted=self.request_attempted,
            page_count=self.page_count,
            raw_record_count=self.raw_record_count,
            parsed_record_count=self.parsed_record_count,
            active_record_count=self.active_record_count,
            category_eligible_count=self.category_eligible_count,
            expiry_eligible_count=None,
            price_liquidity_eligible_count=self.price_liquidity_eligible_count,
            final_eligible_count=self.final_eligible_count,
        )


def _safe_float(value, default=0.0):
    """Convert API values to float safely."""
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _candidate_kalshi_base_urls(environment: Mapping[str, str] | None = None):
    """Resolve configured Kalshi API URL with canonical fallback."""
    values = os.environ if environment is None else environment
    configured = (
        values.get("KALSHI_BASE_URL", KALSHI_CANONICAL_BASE_URL)
        .strip()
        .rstrip("/")
    )
    if not configured:
        configured = KALSHI_CANONICAL_BASE_URL

    if configured == KALSHI_CANONICAL_BASE_URL:
        return [configured]
    return [configured, KALSHI_CANONICAL_BASE_URL]


def _series_ticker(series):
    return str(series.get('ticker') or '').strip().upper()


def _series_sort_key(series):
    return (
        -_safe_float(series.get('volume', 0), 0.0),
        _safe_float(series.get('fee_multiplier', 1), 1.0),
    )


def _select_series_for_fetch(
    target_series,
    series_limit,
    supplemental_series=KALSHI_MAIN_SUPPLEMENTAL_SERIES,
):
    """Select top-N series plus bounded priority supplemental series."""
    sorted_series = sorted(target_series, key=_series_sort_key)
    selected = sorted_series if series_limit <= 0 else list(sorted_series[:series_limit])
    selected_tickers = {_series_ticker(series) for series in selected}
    supplemental_tickers = {
        str(ticker).strip().upper()
        for ticker in supplemental_series
        if str(ticker).strip()
    }

    supplemental_added = set()
    if supplemental_tickers:
        for series in sorted_series:
            ticker = _series_ticker(series)
            if ticker in supplemental_tickers and ticker not in selected_tickers:
                selected.append(series)
                selected_tickers.add(ticker)
                supplemental_added.add(ticker)

    return selected, supplemental_added


def _market_price_value(market, dollars_key, legacy_key):
    """
    Read current Kalshi *_dollars fields first, then legacy cent fields.
    Legacy fields may be cents (e.g. 37) or dollars (e.g. 0.37).

    Returns None if both modern and legacy fields are missing.
    Returns 0.0 if the field is present with a zero value.
    """
    # Prefer modern *_dollars string field
    if dollars_key in market:
        val = market[dollars_key]
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    # Fallback to legacy field
    legacy_raw = market.get(legacy_key)
    if legacy_raw in (None, ""):
        return None

    try:
        legacy_val = float(legacy_raw)
    except (TypeError, ValueError):
        return None
    if legacy_val > 1.0:
        return legacy_val / 100.0
    return legacy_val


def get_kalshi_headers(method, path, api_key, private_key):
    """Generate Kalshi API headers"""
    timestamp = str(int(time.time() * 1000))  # milliseconds!
    path_without_query = path.split('?')[0]
    msg = f"{timestamp}{method}/trade-api/v2{path_without_query}"

    signature = private_key.sign(
        msg.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


def fetch_kalshi_series(api_key, private_key):
    """
    Fetch all series from Kalshi and filter by category
    
    Returns:
        List of series objects in target categories
    """
    try:
        headers = get_kalshi_headers('GET', '/series', api_key, private_key)
        resp = None
        selected_base = None
        for base_url in _candidate_kalshi_base_urls():
            candidate = requests.get(
                f"{base_url}/trade-api/v2/series",
                headers=headers,
                params={'include_volume': 'true'},
                timeout=15
            )
            if candidate.status_code == 200:
                resp = candidate
                selected_base = base_url
                break

            body_preview = candidate.text[:160].replace("\n", " ")
            logger.warning(
                "[KALSHI] Series API failed: status=%s base=%s body=%s",
                candidate.status_code,
                base_url,
                body_preview,
            )
            resp = candidate

        if resp is None or resp.status_code != 200:
            logger.error("Kalshi Series API error: no successful base URL")
            return []
        
        data = resp.json()
        all_series = data.get('series', [])
        
        logger.info(f"[KALSHI] Series discovery: {len(all_series)} total series (base={selected_base})")
        
        # Filter series by category
        target_series = []
        blocked_count = 0
        
        for s in all_series:
            category = s.get('category', '')
            ticker = s.get('ticker', '')
            
            # Check if blocked by prefix first
            if any(ticker.upper().startswith(prefix) for prefix in KALSHI_BLOCKED_PREFIXES):
                blocked_count += 1
                continue
            
            # Check category - blocklist only (2026-03-14)
            if category in KALSHI_BLOCKED_CATEGORIES:
                blocked_count += 1
                continue

            # All non-blocked categories now pass through
            target_series.append(s)
        
        logger.info(f"[KALSHI] Series filter: {len(target_series)} target, {blocked_count} blocked")
        
        # Log target series tickers
        target_tickers = [s.get('ticker') for s in target_series[:20]]
        logger.info(f"[KALSHI] Target series: {target_tickers}")
        
        return target_series
        
    except Exception as e:
        logger.error(f"Kalshi Series API error: {e}")
        return []


def fetch_markets_for_series(series_ticker, api_key, private_key):
    """
    Fetch open markets for a specific series
    
    Args:
        series_ticker: Series ticker (e.g., "KXINXSP5" for S&P 500 range)
        api_key: Kalshi API key
        private_key: RSA private key for signing
    
    Returns:
        List of market objects
    """
    try:
        headers = get_kalshi_headers('GET', '/markets', api_key, private_key)
        resp = None
        for base_url in _candidate_kalshi_base_urls():
            candidate = requests.get(
                f"{base_url}/trade-api/v2/markets",
                headers=headers,
                params={'series_ticker': series_ticker, 'status': 'open', 'limit': 100},
                timeout=15
            )
            if candidate.status_code == 200:
                resp = candidate
                break
            resp = candidate
        
        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else "n/a"
            logger.debug(f"No markets for series {series_ticker}: {status}")
            return []
        
        data = resp.json()
        return data.get('markets', [])
        
    except Exception as e:
        logger.debug(f"Error fetching markets for {series_ticker}: {e}")
        return []


def _request_status_class(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "2XX"
    if 400 <= status_code < 500:
        return "4XX"
    return "5XX"


def _request_discovery_page(
    *,
    path: str,
    params: dict[str, object],
    auth_headers_factory: Callable[[str, str], Mapping[str, str]],
    request_get: Callable,
    environment: Mapping[str, str],
    progress: _DiscoveryProgress,
) -> tuple[dict, str]:
    try:
        headers = auth_headers_factory("GET", path)
    except Exception as exc:
        raise _DiscoveryFailure(
            DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
            "NOT_ATTEMPTED",
        ) from exc

    response = None
    progress.request_attempted = 1
    try:
        for base_url in _candidate_kalshi_base_urls(environment):
            candidate = request_get(
                f"{base_url}/trade-api/v2{path}",
                headers=headers,
                params=params,
                timeout=15,
            )
            response = candidate
            if 200 <= int(candidate.status_code) < 300:
                break
    except requests.exceptions.Timeout as exc:
        raise _DiscoveryFailure(
            DiscoveryOutcome.NETWORK_TIMEOUT,
            "NETWORK_ERROR",
        ) from exc
    except (requests.exceptions.ConnectionError, requests.exceptions.RequestException) as exc:
        raise _DiscoveryFailure(
            DiscoveryOutcome.NETWORK_ERROR,
            "NETWORK_ERROR",
        ) from exc

    if response is None:
        raise _DiscoveryFailure(
            DiscoveryOutcome.NETWORK_ERROR,
            "NETWORK_ERROR",
        )
    status_code = int(response.status_code)
    status_class = _request_status_class(status_code)
    if not 200 <= status_code < 300:
        outcome = (
            DiscoveryOutcome.AUTH_REJECTED
            if status_code in {401, 403}
            else DiscoveryOutcome.HTTP_ERROR
        )
        raise _DiscoveryFailure(outcome, status_class)
    try:
        payload = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError) as exc:
        raise _DiscoveryFailure(
            DiscoveryOutcome.JSON_PARSE_ERROR,
            status_class,
        ) from exc
    if not isinstance(payload, dict):
        raise _DiscoveryFailure(DiscoveryOutcome.SCHEMA_ERROR, status_class)
    return payload, status_class


def _fetch_paginated_collection(
    *,
    path: str,
    collection_field: str,
    params: dict[str, object],
    auth_headers_factory: Callable[[str, str], Mapping[str, str]],
    request_get: Callable,
    environment: Mapping[str, str],
    progress: _DiscoveryProgress,
    count_market_records: bool = False,
    market_record_base: int = 0,
) -> list[dict]:
    records: list[dict] = []
    cursor = ""
    seen_cursors: set[str] = set()
    for page_index in range(MAX_DISCOVERY_PAGES):
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor
        try:
            payload, _status_class = _request_discovery_page(
                path=path,
                params=page_params,
                auth_headers_factory=auth_headers_factory,
                request_get=request_get,
                environment=environment,
                progress=progress,
            )
            if collection_field not in payload:
                raise _DiscoveryFailure(
                    DiscoveryOutcome.SCHEMA_ERROR,
                    "2XX",
                )
            page_records = payload[collection_field]
            if not isinstance(page_records, list) or not all(
                isinstance(record, dict) for record in page_records
            ):
                raise _DiscoveryFailure(
                    DiscoveryOutcome.SCHEMA_ERROR,
                    "2XX",
                )
            next_cursor = payload.get("cursor", "")
            if next_cursor is None:
                next_cursor = ""
            if not isinstance(next_cursor, str):
                raise _DiscoveryFailure(
                    DiscoveryOutcome.SCHEMA_ERROR,
                    "2XX",
                )
        except _DiscoveryFailure as exc:
            if page_index > 0:
                raise _DiscoveryFailure(
                    DiscoveryOutcome.PAGINATION_ERROR,
                    exc.request_status_class,
                ) from exc
            raise

        records.extend(page_records)
        progress.page_count += 1
        if count_market_records:
            progress.raw_record_count = market_record_base + len(records)
        cursor = next_cursor.strip()
        if not cursor:
            return records
        if cursor in seen_cursors:
            raise _DiscoveryFailure(
                DiscoveryOutcome.PAGINATION_ERROR,
                "2XX",
            )
        seen_cursors.add(cursor)
    raise _DiscoveryFailure(
        DiscoveryOutcome.PAGINATION_ERROR,
        "2XX",
    )


def _discovery_failure_result(
    failure: _DiscoveryFailure,
    progress: _DiscoveryProgress,
) -> KalshiDiscoveryResult:
    return KalshiDiscoveryResult(
        outcome=failure.outcome,
        counts=progress.counts(),
        request_status_class=(
            failure.request_status_class
            if failure.request_status_class in REQUEST_STATUS_CLASSES
            else "NETWORK_ERROR"
        ),
    )


def _fetch_kalshi_markets_diagnostic_with_auth_headers(
    *,
    environment: Mapping[str, str],
    auth_headers_factory: Callable[[str, str], Mapping[str, str]],
    request_get: Callable | None = None,
    normalizer: Callable[[dict], dict] = normalize_kalshi_market,
) -> KalshiDiscoveryResult:
    """Run diagnostic discovery through an injected authenticated boundary.

    This core never opens credential files or receives credential values.
    """

    values = environment
    progress = _DiscoveryProgress()
    request_get = request_get or requests.get
    if not callable(auth_headers_factory):
        return KalshiDiscoveryResult(
            outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
            counts=progress.counts(),
        )

    try:
        try:
            series_limit = int(values.get("KALSHI_SERIES_LIMIT", "50") or "50")
        except (TypeError, ValueError):
            series_limit = 50
        try:
            min_liquidity_usd = float(
                values.get("KALSHI_FETCH_MIN_LIQUIDITY_USD", "0") or "0"
            )
        except (TypeError, ValueError):
            min_liquidity_usd = 0.0
        include_categories_raw = str(
            values.get("KALSHI_FETCH_INCLUDE_CATEGORIES", "")
        ).strip()
        include_categories = (
            {
                category.strip().lower()
                for category in include_categories_raw.split(",")
                if category.strip()
            }
            if include_categories_raw
            else None
        )

        target_series = _fetch_paginated_collection(
            path="/series",
            collection_field="series",
            params={"include_volume": "true"},
            auth_headers_factory=auth_headers_factory,
            request_get=request_get,
            environment=values,
            progress=progress,
        )
        if not all(str(series.get("ticker") or "").strip() for series in target_series):
            raise _DiscoveryFailure(DiscoveryOutcome.SCHEMA_ERROR, "2XX")
        target_series = [
            series
            for series in target_series
            if not any(
                _series_ticker(series).startswith(prefix)
                for prefix in KALSHI_BLOCKED_PREFIXES
            )
            and series.get("category", "") not in KALSHI_BLOCKED_CATEGORIES
        ]
        selected_series, supplemental_added = _select_series_for_fetch(
            target_series,
            series_limit,
        )

        raw_markets: list[dict] = []
        progress.raw_record_count = 0
        for series in selected_series:
            series_ticker = _series_ticker(series)
            series_markets = _fetch_paginated_collection(
                path="/markets",
                collection_field="markets",
                params={
                    "series_ticker": series_ticker,
                    "status": "open",
                    "limit": 100,
                },
                auth_headers_factory=auth_headers_factory,
                request_get=request_get,
                environment=values,
                progress=progress,
                count_market_records=True,
                market_record_base=len(raw_markets),
            )
            for market in series_markets:
                market = dict(market)
                market["series_ticker"] = series_ticker
                market["series_category"] = series.get("category")
                market["fee_multiplier"] = series.get("fee_multiplier", 0.07)
                market["series_volume"] = series.get("volume", 0)
                market["_kalshi_fetch_source"] = (
                    "supplemental_series"
                    if series_ticker in supplemental_added
                    else "top_series"
                )
                raw_markets.append(market)
            progress.raw_record_count = len(raw_markets)

        parsed: list[tuple[dict, dict]] = []
        for market in raw_markets:
            if not str(market.get("ticker") or market.get("id") or "").strip():
                raise _DiscoveryFailure(DiscoveryOutcome.SCHEMA_ERROR, "2XX")
            try:
                normalized = normalizer(market)
            except Exception as exc:
                raise _DiscoveryFailure(
                    DiscoveryOutcome.SCHEMA_ERROR,
                    "2XX",
                ) from exc
            if not isinstance(normalized, dict) or not str(
                normalized.get("ticker") or normalized.get("id") or ""
            ).strip():
                raise _DiscoveryFailure(DiscoveryOutcome.SCHEMA_ERROR, "2XX")
            normalized["series_ticker"] = market.get("series_ticker")
            normalized["series_category"] = market.get("series_category")
            normalized["kalshi_fetch_source"] = market.get("_kalshi_fetch_source")
            normalized["fee_multiplier"] = _safe_float(
                market.get("fee_multiplier", 1),
                1.0,
            )
            normalized["fee_type"] = (
                "quadratic"
                if _safe_float(market.get("fee_multiplier", 1), 1.0) < 1
                else "standard"
            )
            parsed.append((market, normalized))
        progress.parsed_record_count = len(parsed)

        active = [
            pair
            for pair in parsed
            if str(pair[0].get("status", "active") or "active").lower()
            in {"active", "open"}
        ]
        progress.active_record_count = len(active)

        price_liquidity: list[tuple[dict, dict]] = []
        for raw, normalized in active:
            yes_bid_price = _market_price_value(
                raw,
                "yes_bid_dollars",
                "yes_bid",
            )
            yes_ask_price = _market_price_value(
                raw,
                "yes_ask_dollars",
                "yes_ask",
            )
            if yes_ask_price is None or yes_ask_price <= 0:
                yes_ask_price = _market_price_value(
                    raw,
                    "last_price_dollars",
                    "last_price",
                )
                if yes_ask_price is None or yes_ask_price <= 0:
                    continue
            yes_price = (
                (yes_bid_price + yes_ask_price) / 2.0
                if yes_bid_price is not None and yes_bid_price > 0
                else yes_ask_price
            )
            reported_liquidity_usd = _safe_float(
                raw.get("liquidity_dollars", 0),
                0.0,
            )
            open_interest_units = _safe_float(
                raw.get("open_interest_fp", raw.get("open_interest", 0)),
                0.0,
            )
            liquidity_usd = (
                reported_liquidity_usd
                if reported_liquidity_usd > 0
                else open_interest_units * yes_price
            )
            if min_liquidity_usd > 0 and liquidity_usd < min_liquidity_usd:
                continue
            price_liquidity.append((raw, normalized))
        progress.price_liquidity_eligible_count = len(price_liquidity)

        if include_categories is None:
            category_eligible = price_liquidity
        else:
            category_eligible = [
                pair
                for pair in price_liquidity
                if str(pair[0].get("series_category", "")).strip().lower()
                in include_categories
            ]
        progress.category_eligible_count = len(category_eligible)

        final_pairs = [
            pair
            for pair in category_eligible
            if not any(
                str(pair[0].get("ticker") or "").upper().startswith(prefix)
                for prefix in KALSHI_BLOCKED_PREFIXES
            )
        ]
        final_pairs.sort(
            key=lambda pair: -_safe_float(
                pair[0].get("volume_24h_fp", pair[0].get("volume_24h", 0)),
                0.0,
            )
        )
        markets = tuple(normalized for _raw, normalized in final_pairs)
        progress.final_eligible_count = len(markets)
        return KalshiDiscoveryResult(
            outcome=(
                DiscoveryOutcome.SUCCESS_NONEMPTY
                if markets
                else DiscoveryOutcome.SUCCESS_EMPTY
            ),
            markets=markets,
            counts=progress.counts(),
            request_status_class="2XX",
        )
    except _DiscoveryFailure as failure:
        return _discovery_failure_result(failure, progress)
    except Exception:
        return KalshiDiscoveryResult(
            outcome=DiscoveryOutcome.INTERNAL_DISCOVERY_ERROR,
            counts=progress.counts(),
            request_status_class=(
                "2XX" if progress.request_attempted else "NOT_ATTEMPTED"
            ),
        )


def _fetch_kalshi_markets_diagnostic_authenticated(
    *,
    environment: Mapping[str, str],
    api_key: str,
    private_key: object,
    request_get: Callable | None = None,
    header_factory: Callable = get_kalshi_headers,
    normalizer: Callable[[dict], dict] = normalize_kalshi_market,
) -> KalshiDiscoveryResult:
    """Preserve the established value-based authenticated caller contract."""

    configured_api_key = str(api_key).strip()
    if not configured_api_key or private_key is None:
        return KalshiDiscoveryResult(
            outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
            counts=_DiscoveryProgress().counts(),
        )

    def auth_headers_factory(method: str, path: str) -> Mapping[str, str]:
        return header_factory(method, path, configured_api_key, private_key)

    return _fetch_kalshi_markets_diagnostic_with_auth_headers(
        environment=environment,
        auth_headers_factory=auth_headers_factory,
        request_get=request_get,
        normalizer=normalizer,
    )


def fetch_kalshi_markets_diagnostic(
    *,
    environment: Mapping[str, str] | None = None,
    api_key: str | None = None,
    private_key: object | None = None,
    request_get: Callable | None = None,
    header_factory: Callable = get_kalshi_headers,
    normalizer: Callable[[dict], dict] = normalize_kalshi_market,
) -> KalshiDiscoveryResult:
    """Fetch markets with bounded diagnostics for established callers.

    The existing file-based fallback remains here for compatibility. The
    purpose-built redacted one-shot command bypasses this wrapper and enters
    the authenticated core only after resolving inherited in-memory material.
    """

    values = os.environ if environment is None else environment
    configured_api_key = api_key or str(values.get("KALSHI_KEY", "")).strip()
    if not configured_api_key:
        return KalshiDiscoveryResult(
            outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
            counts=_DiscoveryProgress().counts(),
        )
    if private_key is None:
        secret_file = str(
            values.get("KALSHI_SECRET_FILE", "./kalshi_private_key.pem")
        ).strip()
        if not secret_file:
            return KalshiDiscoveryResult(
                outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
                counts=_DiscoveryProgress().counts(),
            )
        try:
            with open(secret_file, "rb") as handle:
                private_key = serialization.load_pem_private_key(
                    handle.read(),
                    password=None,
                )
        except (OSError, TypeError, ValueError):
            return KalshiDiscoveryResult(
                outcome=DiscoveryOutcome.AUTH_CONFIGURATION_MISSING,
                counts=_DiscoveryProgress().counts(),
            )
    return _fetch_kalshi_markets_diagnostic_authenticated(
        environment=values,
        api_key=configured_api_key,
        private_key=private_key,
        request_get=request_get,
        header_factory=header_factory,
        normalizer=normalizer,
    )


def fetch_kalshi_markets():
    """
    Fetch open markets from Kalshi API - FIXED VERSION
    
    Now uses Series API to discover financial/economic/political markets
    instead of fetching ALL markets (which were 100% esports)
    """
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
    
    if not api_key:
        logger.warning("KALSHI_KEY not set")
        return []
    
    try:
        series_limit = int(os.getenv("KALSHI_SERIES_LIMIT", "50") or "50")
    except ValueError:
        series_limit = 50
    try:
        min_liquidity_usd = float(os.getenv("KALSHI_FETCH_MIN_LIQUIDITY_USD", "0") or "0")
    except ValueError:
        min_liquidity_usd = 0.0

    include_categories_raw = os.getenv("KALSHI_FETCH_INCLUDE_CATEGORIES", "").strip()
    include_categories = {
        c.strip().lower()
        for c in include_categories_raw.split(",")
        if c.strip()
    } if include_categories_raw else None

    try:
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        
        # Step 1: Discover target series by category
        target_series = fetch_kalshi_series(api_key, private_key)
        
        if not target_series:
            logger.warning("[KALSHI] No target series found - FAILING CLOSED (no fallback to sports)")
            return []
        
        # Step 2: Apply series limit and fetch markets from each selected series
        all_markets = []
        series_with_markets = 0

        selected_series, supplemental_added = _select_series_for_fetch(
            target_series,
            series_limit,
        )
        logger.info(
            f"[KALSHI FILTER] Series limit: {len(target_series)} -> {len(selected_series)} "
            f"(series_limit={series_limit}, supplemental_added={sorted(supplemental_added)})"
        )

        for series in selected_series:
            series_ticker = series.get('ticker')
            markets = fetch_markets_for_series(series_ticker, api_key, private_key)
            
            if markets:
                series_with_markets += 1
                
                # Add series metadata to each market
                for m in markets:
                    m['series_ticker'] = series_ticker
                    m['series_category'] = series.get('category')
                    m['fee_multiplier'] = series.get('fee_multiplier', 0.07)
                    m['series_volume'] = series.get('volume', 0)
                    m['_kalshi_fetch_source'] = (
                        'supplemental_series'
                        if _series_ticker(series) in supplemental_added
                        else 'top_series'
                    )
                
                all_markets.extend(markets)
        
        raw_count = len(all_markets)
        logger.info(f"[KALSHI FILTER] Raw API response count: {raw_count}")

        # Step 3: Status filter (API currently uses 'active', keep compatibility with 'open')
        status_filtered = []
        for m in all_markets:
            status = str(m.get('status', 'active') or 'active').lower()
            if status in {'active', 'open'}:
                status_filtered.append(m)
        logger.info(
            f"[KALSHI FILTER] After status filter: {len(status_filtered)} "
            f"(kept statuses: active/open)"
        )

        # Step 4: yes_ask > 0 filter and normalized price selection
        priced_markets = []
        ask_only_count = 0
        ask_from_last_trade_count = 0
        for m in status_filtered:
            yes_bid_price = _market_price_value(m, 'yes_bid_dollars', 'yes_bid')
            yes_ask_price = _market_price_value(m, 'yes_ask_dollars', 'yes_ask')

            # If ask is missing, try last_price; if also missing, skip.
            # Present-zero ask (0.0) is kept as-is (rare but valid).
            if yes_ask_price is None or yes_ask_price <= 0:
                last_price = _market_price_value(m, 'last_price_dollars', 'last_price')
                if last_price is None or last_price <= 0:
                    continue
                yes_ask_price = last_price
                ask_from_last_trade_count += 1

            # Ask-only books are common; midpoint with bid=0 underprices by 50%.
            # If bid is missing, treat as ask-only. Present-zero bid is included.
            if yes_bid_price is not None and yes_bid_price > 0:
                yes_price = (yes_bid_price + yes_ask_price) / 2.0
            else:
                yes_price = yes_ask_price
                if yes_bid_price is None:
                    ask_only_count += 1

            m['_yes_bid_price'] = yes_bid_price
            m['_yes_ask_price'] = yes_ask_price
            m['_yes_price'] = yes_price
            priced_markets.append(m)
        logger.info(
            f"[KALSHI FILTER] After yes_ask > 0 filter: {len(priced_markets)} "
            f"(ask-only books: {ask_only_count}, ask-from-last-trade: {ask_from_last_trade_count})"
        )

        # Step 5: Liquidity filter (default disabled; keep data flow open for strategy-level logic)
        liquidity_filtered = []
        for m in priced_markets:
            reported_liquidity_usd = _safe_float(m.get('liquidity_dollars', 0), 0.0)
            open_interest_units = _safe_float(
                m.get('open_interest_fp', m.get('open_interest', 0)),
                0.0,
            )
            implied_liquidity_usd = open_interest_units * m['_yes_price']
            liquidity_usd = reported_liquidity_usd if reported_liquidity_usd > 0 else implied_liquidity_usd
            m['_liquidity_usd'] = liquidity_usd

            volume_24h = _safe_float(m.get('volume_24h_fp', m.get('volume_24h', 0)), 0.0)
            if volume_24h <= 0:
                volume_24h = _safe_float(m.get('volume_fp', m.get('volume', 0)), 0.0)
            m['_volume_24h'] = volume_24h

            if min_liquidity_usd > 0 and liquidity_usd < min_liquidity_usd:
                continue
            liquidity_filtered.append(m)
        logger.info(
            f"[KALSHI FILTER] After liquidity filter: {len(liquidity_filtered)} "
            f"(min_liquidity_usd={min_liquidity_usd})"
        )

        # Step 6: Category filter (default disabled to include sports/crypto/high-liquidity markets)
        if include_categories is None:
            category_filtered = liquidity_filtered
            logger.info(
                f"[KALSHI FILTER] After category filter: {len(category_filtered)} "
                "(filter disabled; all categories included)"
            )
        else:
            category_filtered = []
            for m in liquidity_filtered:
                category = str(m.get('series_category', '')).strip().lower()
                if category in include_categories:
                    category_filtered.append(m)
            logger.info(
                f"[KALSHI FILTER] After category filter: {len(category_filtered)} "
                f"(include_categories={sorted(include_categories)})"
            )

        # Step 7: Final safety filter - reject blocked prefixes
        filtered_markets = []
        for m in category_filtered:
            ticker = m.get('ticker', '')
            if not any(ticker.upper().startswith(prefix) for prefix in KALSHI_BLOCKED_PREFIXES):
                filtered_markets.append(m)

        # Step 8: Sort by available liquidity/volume proxies (most active first)
        filtered_markets.sort(key=lambda m: -_safe_float(m.get('_volume_24h', 0), 0.0))

        # Log top 10
        top_10 = [
            (
                m.get('ticker'),
                _safe_float(m.get('_volume_24h', 0), 0.0),
                _safe_float(m.get('_yes_ask_price', 0), 0.0),
                _safe_float(m.get('_yes_bid_price', 0), 0.0),
            )
            for m in filtered_markets[:10]
        ]
        logger.info(f"[KALSHI] Top 10 by volume: {top_10}")

        # Step 9: Format markets for strategy use via normalization layer
        markets = []
        for m in filtered_markets:
            norm = normalize_kalshi_market(m)
            # Preserve series metadata not in the raw normalizer output
            norm["series_ticker"] = m.get('series_ticker')
            norm["series_category"] = m.get('series_category')
            norm["kalshi_fetch_source"] = m.get('_kalshi_fetch_source')
            norm["fee_multiplier"] = _safe_float(m.get('fee_multiplier', 1), 1.0)
            norm["fee_type"] = "quadratic" if _safe_float(m.get('fee_multiplier', 1), 1.0) < 1 else "standard"
            markets.append(norm)

        if len(markets) == 0:
            logger.warning("[KALSHI] No markets with active trading prices. "
                          "This is normal outside market hours or if markets have closed. "
                          f"Found {len(all_markets)} markets from API but none have yes_ask_dollars > 0.")
        else:
            logger.info(f"[KALSHI FILTER] Final returned count: {len(markets)}")

        return markets
        
    except Exception as e:
        logger.error(f"Kalshi API error: {e}")
        return []


# Legacy function for backwards compatibility
def fetch_kalshi_markets_legacy():
    """
    LEGACY - Fetches all markets without category filtering
    This was returning 100% esports markets
    """
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
    
    if not api_key:
        return []
    
    try:
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        
        headers = get_kalshi_headers('GET', '/markets', api_key, private_key)
        resp = requests.get(
            'https://api.elections.kalshi.com/trade-api/v2/markets',
            headers=headers,
            params={'status': 'open', 'limit': 100},
            timeout=10
        )
        
        if resp.status_code == 200:
            data = resp.json() if resp.text.strip() else {"markets": []}
            markets = []
            for m in data.get('markets', []):
                ticker = m.get('ticker', '')
                yes_bid_cents = float(m.get('yes_bid_dollars', 0) or 0)
                yes_ask_cents = float(m.get('yes_ask_dollars', 0) or 0)
                if yes_ask_cents <= 0:
                    continue
                yes_price = ((yes_bid_cents + yes_ask_cents) / 2)
                no_price = 1.0 - yes_price
                liquidity_usd = m.get('open_interest', 0) * yes_price
                markets.append({
                    "id": ticker,
                    "question": m.get('short_name', ticker),
                    "odds": {"yes": yes_price, "no": no_price},
                    "liquidity_usd": liquidity_usd,
                    "hours_to_end": 48
                })
            return markets
    except Exception as e:
        print(f"Kalshi API error: {e}")

    return []


def kalshi_debug_discovery():
    """
    One-time diagnostic: dump what the API actually returns.

    Use this to discover what categories the API returns so we can
    update KALSHI_TARGET_CATEGORIES accordingly.

    Run with:
        python3 -c "from utils.kalshi import kalshi_debug_discovery; kalshi_debug_discovery()"
    """
    import json
    logger.info("[KALSHI DEBUG] Starting discovery...")

    # Get ALL series
    url = "https://api.elections.kalshi.com/trade-api/v2/series"
    resp = requests.get(url, params={"include_volume": True}, timeout=15)
    series_data = resp.json()

    categories = {}
    for s in series_data.get("series", []):
        cat = s.get("category", "UNKNOWN")
        categories.setdefault(cat, []).append({
            "ticker": s["ticker"],
            "title": s.get("title", ""),
            "volume": s.get("volume", 0),
        })

    # Write diagnostic output
    with open("/opt/slimy/pm_updown_bot_bundle/kalshi_debug.json", "w") as f:
        json.dump({
            "total_series": len(series_data.get("series", [])),
            "categories": {k: len(v) for k, v in categories.items()},
            "category_details": categories,
        }, f, indent=2)

    logger.info(f"[KALSHI DEBUG] Categories found: {list(categories.keys())}")

    # Get first 100 open markets (no filter)
    url2 = "https://api.elections.kalshi.com/trade-api/v2/markets"
    resp2 = requests.get(url2, params={"status": "open", "limit": 100}, timeout=15)
    markets = resp2.json().get("markets", [])

    ticker_prefixes = {}
    for m in markets:
        prefix = m["ticker"][:6] if len(m["ticker"]) >= 6 else m["ticker"]
        ticker_prefixes.setdefault(prefix, []).append(m["ticker"])

    with open("/opt/slimy/pm_updown_bot_bundle/kalshi_markets_debug.json", "w") as f:
        json.dump({
            "total_open_markets": len(markets),
            "ticker_prefix_counts": {k: len(v) for k, v in ticker_prefixes.items()},
            "sample_markets": [{"ticker": m["ticker"], "title": m.get("title", ""), "volume_24h": m.get("volume_24h", 0)} for m in markets[:20]],
        }, f, indent=2)

    logger.info(f"[KALSHI DEBUG] Total open markets: {len(markets)}")
    logger.info(f"[KALSHI DEBUG] Top ticker prefixes: {dict(list(ticker_prefixes.items())[:10])}")
    logger.info(f"[KALSHI DEBUG] Output written to kalshi_debug.json and kalshi_markets_debug.json")

    return categories


def get_kalshi_balance():
    """
    Fetch account balance from Kalshi API.

    Returns:
        float: Balance in USD. Returns 0.0 on error (with WARNING logged).
    """
    api_key = os.getenv("KALSHI_KEY")
    secret_file = os.getenv("KALSHI_SECRET_FILE", "./kalshi_private_key.pem")

    if not api_key:
        logger.warning("[WALLET] KALSHI_KEY not set, cannot fetch balance")
        return 0.0

    try:
        with open(secret_file, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    except Exception as e:
        logger.warning(f"[WALLET] Failed to load Kalshi private key: {e}")
        return 0.0

    path = "/trade-api/v2/portfolio/balance"
    timestamp = str(int(time.time() * 1000))
    msg = f"{timestamp}GET{path}"
    signature = private_key.sign(
        msg.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()
    headers = {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }

    try:
        resp = requests.get(
            f"https://api.elections.kalshi.com{path}",
            headers=headers,
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"[WALLET] Balance fetch request failed: {e}")
        return 0.0

    if resp.status_code != 200:
        body_preview = resp.text[:200].replace("\n", " ")
        logger.warning(
            f"[WALLET] Balance fetch failed: status={resp.status_code} body={body_preview}"
        )
        return 0.0

    try:
        data = resp.json()
        # Try modern API field first (dollars/fp as string)
        balance_dollars = data.get("balance_dollars") or data.get("balance_fp")
        if balance_dollars is not None:
            try:
                balance_usd = float(balance_dollars)
                logger.info(f"[WALLET] Fetched balance (modern): ${balance_usd:.2f}")
                return balance_usd
            except (ValueError, TypeError):
                pass
        # Fallback to legacy cents field
        balance_cents = data.get("balance")
        if balance_cents is not None:
            try:
                balance_usd = float(balance_cents) / 100.0
                logger.info(f"[WALLET] Fetched balance (legacy cents): ${balance_usd:.2f}")
                return balance_usd
            except (ValueError, TypeError):
                pass
        # No recognizable balance field
        logger.warning(f"[WALLET] No recognizable balance field in response: {list(data.keys())}")
        return 0.0
    except Exception as e:
        logger.warning(f"[WALLET] Balance parse failed: {e} body={resp.text[:200]}")
        return 0.0


# Alias for backwards compatibility
get_kalshi_markets = fetch_kalshi_markets
