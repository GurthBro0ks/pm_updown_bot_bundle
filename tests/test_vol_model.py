from datetime import date

import pytest

from providers import vol_model


@pytest.fixture(autouse=True)
def clear_vol_cache():
    vol_model.clear_cache()
    yield
    vol_model.clear_cache()


def _market_data(current_price=7500.0):
    return {
        "current_price": current_price,
        "closes": [current_price * (1.0 + (i - 15) * 0.0001) for i in range(30)],
    }


def _patch_market(monkeypatch, current_price=7500.0, annual_vol=0.012):
    monkeypatch.setattr(vol_model, "_fetch_market_data", lambda symbol: _market_data(current_price))
    monkeypatch.setattr(vol_model, "_compute_realized_vol_10d", lambda closes: annual_vol)


def test_sp500_probability(monkeypatch):
    _patch_market(monkeypatch, current_price=7500.0, annual_vol=0.012)

    result = vol_model.compute_vol_probability("KXINX", 7525.0, 1.0)

    assert result is not None
    assert result["vol_prob"] < 0.10
    assert result["current_price"] == 7500.0
    assert result["realized_vol_10d"] == 0.012
    assert result["model"] == "log_normal_vol"


def test_sp500_at_the_money(monkeypatch):
    _patch_market(monkeypatch, current_price=7500.0, annual_vol=0.012)

    result = vol_model.compute_vol_probability("KXINX", 7500.0, 1.0)

    assert result is not None
    assert result["vol_prob"] == pytest.approx(0.5, abs=0.001)


def test_sp500_deep_in_the_money(monkeypatch):
    _patch_market(monkeypatch, current_price=7500.0, annual_vol=0.012)

    result = vol_model.compute_vol_probability("KXINX", 7000.0, 1.0)

    assert result is not None
    assert result["vol_prob"] > 0.99


def test_ticker_parsing():
    parsed = vol_model.parse_kalshi_index_ticker("KXINXU-26MAY08H1600-T7374.9999")

    assert parsed == {
        "prefix": "KXINX",
        "strike": 7374.9999,
        "expiry_date": date(2026, 5, 8),
        "direction": "above",
    }


def test_ticker_parsing_ndx():
    parsed = vol_model.parse_kalshi_index_ticker("KXNDXU-26MAY08H1600-T20000")

    assert parsed is not None
    assert parsed["prefix"] == "KXNDX"


def test_blending():
    assert vol_model.blend_probability(ai_prob=0.70, vol_prob=0.30) == pytest.approx(0.46)


def test_shrinkage_fallback():
    assert vol_model.apply_probability_shrinkage(0.80) == pytest.approx(0.65)


def test_shrinkage_neutral():
    assert vol_model.apply_probability_shrinkage(0.50) == pytest.approx(0.50)


def test_unknown_ticker():
    assert vol_model.parse_kalshi_index_ticker("KXHIGHNY-26MAY08H1600-T85") is None
    assert vol_model.compute_vol_probability("KXHIGHNY", 85.0, 1.0) is None


def test_cache(monkeypatch):
    calls = {"count": 0}

    def fake_fetch(symbol):
        calls["count"] += 1
        return _market_data(7500.0)

    monkeypatch.setattr(vol_model, "_fetch_market_data", fake_fetch)
    monkeypatch.setattr(vol_model, "_compute_realized_vol_10d", lambda closes: 0.012)

    first = vol_model.compute_vol_probability("KXINX", 7525.0, 1.0)
    second = vol_model.compute_vol_probability("KXINX", 7525.0, 1.0)

    assert first == second
    assert calls["count"] == 1
