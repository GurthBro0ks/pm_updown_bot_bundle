# Kalshi API Field Migration

## What Changed

Kalshi deprecated legacy integer-cent price fields on **March 12, 2026**.

### Old Fields (Deprecated)
- `yes_bid`, `yes_ask`, `no_bid`, `no_ask` — integer cents (e.g. `37` = $0.37)
- `volume`, `volume_24h`, `open_interest` — integer counts
- `last_price` — integer cents

### New Fields (Current)
- `yes_bid_dollars`, `yes_ask_dollars`, `no_bid_dollars`, `no_ask_dollars` — **strings** (e.g. `"0.1200"`)
- `volume_fp`, `volume_24h_fp`, `open_interest_fp` — **strings** (e.g. `"10000.00"`)
- `last_price_dollars` — **string**

**Critical:** The new `*_dollars` and `*_fp` fields arrive as **strings**, not floats. They must be parsed via `Decimal` first, then converted to `float` at the normalized compatibility boundary.

## Why the Normalization Layer Exists

The `utils/kalshi_normalize.py` module provides `normalize_kalshi_market()` to:
1. Prefer modern dollar/fp fields when present
2. Fall back to legacy cent fields only when modern fields are missing
3. Correctly divide legacy cents by 100.0
4. Distinguish **missing** fields (`None`) from **present zero** (`0.0`)
5. Track which field path was used via `raw_field_source` metadata

This allows existing strategy code to continue reading stable keys like `market["odds"]["yes"]`, `market["liquidity_usd"]`, etc., regardless of which API field shape Kalshi returns.

## Input Field Mapping

| Old Field | New Field | Normalized Key | Conversion |
|-----------|-----------|----------------|------------|
| `yes_bid` (cents) | `yes_bid_dollars` (string) | `yes_bid` | None (dollars) or `/100` (cents) |
| `yes_ask` (cents) | `yes_ask_dollars` (string) | `yes_ask` | None (dollars) or `/100` (cents) |
| `no_bid` (cents) | `no_bid_dollars` (string) | `no_bid` | None (dollars) or `/100` (cents) |
| `no_ask` (cents) | `no_ask_dollars` (string) | `no_ask` | None (dollars) or `/100` (cents) |
| `volume` | `volume_fp` (string) | `volume` | None |
| `volume_24h` | `volume_24h_fp` (string) | `volume_24h` | None |
| `open_interest` | `open_interest_fp` (string) | `open_interest` | None |
| N/A | `liquidity_dollars` (string) | `liquidity_usd` | None |

## Normalized Output Schema

```python
{
  "id": str,                    # ticker
  "ticker": str,
  "question": str,              # short_name or title
  "odds": {
    "yes": float|None,          # midpoint or best available
    "no": float|None            # 1.0 - yes
  },
  "yes_bid": float|None,
  "yes_ask": float|None,
  "no_bid": float|None,
  "no_ask": float|None,
  "volume": float|None,
  "volume_24h": float|None,
  "open_interest": float|None,
  "close_time": str|None,
  "hours_to_end": float|None,
  "liquidity_usd": float|None,  # reported or derived from OI * midprice
  "fees_pct": float,
  "raw_field_source": {
    "yes_bid": "yes_bid_dollars|yes_bid_legacy_cents|yes_bid_legacy_dollars|missing",
    ...
  }
}
```

## How to Run Tests

```bash
cd /opt/slimy/pm_updown_bot_bundle
python3 -m pytest tests/test_kalshi_normalize.py -v
```

## Venue Argument Status

`--venue` was added to `runner.py` argparse on **2026-05-02**.

```bash
python3 runner.py --mode shadow --venue kalshi
```

Valid choices: `kalshi`, `ibkr`. `polymarket` is rejected at runtime (not yet wired).
Default: `kalshi`.

## Remaining Unresolved Questions

1. **Filtering bug:** `utils/kalshi.py` `_market_price_value()` still uses `> 0` truthiness checks. This is pre-existing and only affects the filtering step, not the normalized output.
2. **Legacy code:** `.removed_20260227/` and `proofs/backups/` contain old code with legacy field references. These are archived and not in the active execution path.
3. **Strategy code:** `strategies/kalshi_optimize.py` uses `volume_24h` and `liquidity_usd` from the normalized dict — these are the **internal** keys, not the raw API keys, so they are correct.

## Files Changed

- `runner.py` — added `--venue` arg, replaced `fetch_kalshi_markets()` with delegation to `utils.kalshi`
- `utils/kalshi.py` — wired `normalize_kalshi_market()` into `fetch_kalshi_markets()` output formatting
- `utils/kalshi_normalize.py` — **new** normalization layer
- `tests/test_kalshi_normalize.py` — **new** test suite (32 tests, all passing)
- `scripts/audit_kalshi_fields.py` — **new** field audit tool
- `scripts/kalshi_field_smoke.py` — **new** live API smoke test
