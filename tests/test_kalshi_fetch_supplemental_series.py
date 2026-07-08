from utils import kalshi


def _series(ticker, volume, category="Financials", fee_multiplier=1):
    return {
        "ticker": ticker,
        "volume": volume,
        "category": category,
        "fee_multiplier": fee_multiplier,
    }


def test_supplemental_series_outside_top_n_is_selected():
    selected, supplemental_added = kalshi._select_series_for_fetch(
        [
            _series("KXTOP1", 100),
            _series("KXTOP2", 90),
            _series("KXNASDAQ100U", 0),
        ],
        series_limit=2,
    )

    tickers = [series["ticker"] for series in selected]
    assert tickers == ["KXTOP1", "KXTOP2", "KXNASDAQ100U"]
    assert supplemental_added == {"KXNASDAQ100U"}


def test_supplemental_series_is_deduped_when_already_top_n():
    selected, supplemental_added = kalshi._select_series_for_fetch(
        [
            _series("KXNASDAQ100U", 100),
            _series("KXTOP2", 90),
            _series("KXTOP3", 80),
        ],
        series_limit=2,
    )

    tickers = [series["ticker"] for series in selected]
    assert tickers == ["KXNASDAQ100U", "KXTOP2"]
    assert tickers.count("KXNASDAQ100U") == 1
    assert supplemental_added == set()


def test_supplemental_series_marker_is_preserved_through_normalization(monkeypatch):
    def fake_fetch_series(api_key, private_key):
        return [
            _series("KXTOP1", 100),
            _series("KXNASDAQ100U", 0),
        ]

    def fake_fetch_markets_for_series(series_ticker, api_key, private_key):
        return [
            {
                "ticker": f"{series_ticker}-26JUL08H1600-T29199.99",
                "short_name": "public title",
                "yes_ask_dollars": "0.50",
                "yes_bid_dollars": "0.49",
                "volume_24h_fp": "10",
                "open_interest_fp": "10",
                "close_time": "2026-07-08T21:00:00Z",
                "status": "active",
            }
        ]

    monkeypatch.setenv("KALSHI_KEY", "fake-key-id")
    monkeypatch.setenv("KALSHI_SERIES_LIMIT", "1")
    monkeypatch.setattr(kalshi, "fetch_kalshi_series", fake_fetch_series)
    monkeypatch.setattr(kalshi, "fetch_markets_for_series", fake_fetch_markets_for_series)
    monkeypatch.setattr(kalshi.serialization, "load_pem_private_key", lambda *args, **kwargs: object())
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: _FakeKeyFile())

    markets = kalshi.fetch_kalshi_markets()

    by_series = {market["series_ticker"]: market for market in markets}
    assert by_series["KXTOP1"]["kalshi_fetch_source"] == "top_series"
    assert by_series["KXNASDAQ100U"]["kalshi_fetch_source"] == "supplemental_series"


class _FakeKeyFile:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b"fake-key"
