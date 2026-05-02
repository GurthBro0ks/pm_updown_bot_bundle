"""
Kalshi API Normalization Layer

Handles the March 2026 migration from legacy integer-cent fields
to dollar/fixed-point string fields.

All *_dollars and *_fp fields arrive as STRINGS (e.g. "0.1200", "10.00").
Parse with Decimal first, then convert to float only at the normalized
compatibility boundary.

Rules:
- Prefer *_dollars fields for prices when present.
- Prefer *_fp fields for volume/OI/counts when present.
- Fall back to legacy integer cents ONLY if dollar fields are missing.
- Missing fields → None, NOT zero.
- Zero that is actually present in the API → 0.0, not None.
- Test: if field key exists in dict and value is not None, it is present even if 0.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional


def _parse_decimal_string(value) -> Optional[Decimal]:
    """Parse a string or numeric value to Decimal. Returns None for missing/invalid."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def _field_present(raw: dict, key: str) -> bool:
    """Check if a field key exists in dict and value is not None.

    This is the truth gate for distinguishing 'missing' from 'present zero'.
    """
    return key in raw and raw[key] is not None


def _read_price(raw: dict, dollars_key: str, legacy_key: str, source_tag: str) -> tuple:
    """
    Read a price field preferring *_dollars, falling back to legacy cents.

    Returns (float_value, source_string)
    - float_value: float or None
    - source_string: one of the source tag constants
    """
    # Prefer modern *_dollars string field
    if _field_present(raw, dollars_key):
        dec = _parse_decimal_string(raw[dollars_key])
        if dec is not None:
            return float(dec), f"{source_tag}_dollars"

    # Fallback to legacy integer cents
    if _field_present(raw, legacy_key):
        dec = _parse_decimal_string(raw[legacy_key])
        if dec is not None:
            # Legacy fields may already be in dollars (heuristic: > 1.0 means cents)
            val = float(dec)
            if val > 1.0:
                return val / 100.0, f"{source_tag}_legacy_cents"
            return val, f"{source_tag}_legacy_dollars"

    return None, f"{source_tag}_missing"


def _read_fp_or_legacy(raw: dict, fp_key: str, legacy_key: str, source_tag: str) -> tuple:
    """
    Read a volume/OI/count field preferring *_fp, falling back to legacy.

    Returns (float_value, source_string)
    - float_value: float or None
    - source_string: one of the source tag constants
    """
    # Prefer modern *_fp string field
    if _field_present(raw, fp_key):
        dec = _parse_decimal_string(raw[fp_key])
        if dec is not None:
            return float(dec), f"{source_tag}_fp"

    # Fallback to legacy field
    if _field_present(raw, legacy_key):
        dec = _parse_decimal_string(raw[legacy_key])
        if dec is not None:
            return float(dec), f"{source_tag}_legacy"

    return None, f"{source_tag}_missing"


def _hours_to_end(raw: dict) -> Optional[float]:
    """Compute hours_to_end from close_time or expiration_time if available."""
    import datetime
    close_time = raw.get('close_time') or raw.get('expiration_time')
    if not close_time:
        return None
    try:
        # Try ISO format first
        if isinstance(close_time, str):
            if close_time.endswith('Z'):
                close_time = close_time[:-1] + '+00:00'
            dt = datetime.datetime.fromisoformat(close_time)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            diff = (dt - now).total_seconds() / 3600.0
            return max(0.0, diff)
    except Exception:
        pass
    return None


def normalize_kalshi_market(raw: dict) -> dict:
    """
    Normalize a raw Kalshi API market dict to the internal strategy format.

    Args:
        raw: A single market object from Kalshi API (e.g. from /trade-api/v2/markets)

    Returns:
        Normalized dict with stable keys used by strategies.
    """
    ticker = raw.get('ticker', '') or raw.get('id', '')
    question = raw.get('short_name') or raw.get('title') or ticker

    # Price fields
    yes_bid, yes_bid_src = _read_price(raw, 'yes_bid_dollars', 'yes_bid', 'yes_bid')
    yes_ask, yes_ask_src = _read_price(raw, 'yes_ask_dollars', 'yes_ask', 'yes_ask')
    no_bid, no_bid_src = _read_price(raw, 'no_bid_dollars', 'no_bid', 'no_bid')
    no_ask, no_ask_src = _read_price(raw, 'no_ask_dollars', 'no_ask', 'no_ask')
    last_price, last_price_src = _read_price(raw, 'last_price_dollars', 'last_price', 'last_price')

    # Volume / OI fields
    volume, volume_src = _read_fp_or_legacy(raw, 'volume_fp', 'volume', 'volume')
    volume_24h, volume_24h_src = _read_fp_or_legacy(raw, 'volume_24h_fp', 'volume_24h', 'volume_24h')
    open_interest, open_interest_src = _read_fp_or_legacy(raw, 'open_interest_fp', 'open_interest', 'open_interest')

    # Compute yes price (midpoint or best available)
    if yes_bid is not None and yes_ask is not None:
        yes_price = (yes_bid + yes_ask) / 2.0
    elif yes_ask is not None:
        yes_price = yes_ask
    elif last_price is not None:
        yes_price = last_price
    elif yes_bid is not None:
        yes_price = yes_bid
    else:
        yes_price = None

    if yes_price is not None:
        no_price = 1.0 - yes_price
    else:
        no_price = None

    # Liquidity: prefer reported, else derive from OI * midprice
    reported_liquidity = _parse_decimal_string(raw.get('liquidity_dollars'))
    if reported_liquidity is not None:
        liquidity_usd = float(reported_liquidity)
    elif open_interest is not None and yes_price is not None:
        liquidity_usd = open_interest * yes_price
    else:
        liquidity_usd = None

    # Fees
    fee_multiplier = raw.get('fee_multiplier')
    if fee_multiplier is not None:
        try:
            fees_pct = float(fee_multiplier)
        except (TypeError, ValueError):
            fees_pct = 0.07
    else:
        fees_pct = 0.07

    hours_to_end = _hours_to_end(raw)
    close_time = raw.get('close_time') or raw.get('expiration_time')

    return {
        "id": ticker,
        "ticker": ticker,
        "question": question,
        "odds": {
            "yes": yes_price,
            "no": no_price,
        },
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "volume": volume,
        "volume_24h": volume_24h,
        "open_interest": open_interest,
        "close_time": close_time,
        "hours_to_end": hours_to_end,
        "liquidity_usd": liquidity_usd,
        "fees_pct": fees_pct,
        "raw_field_source": {
            "yes_bid": yes_bid_src,
            "yes_ask": yes_ask_src,
            "no_bid": no_bid_src,
            "no_ask": no_ask_src,
            "last_price": last_price_src,
            "volume": volume_src,
            "volume_24h": volume_24h_src,
            "open_interest": open_interest_src,
        }
    }
