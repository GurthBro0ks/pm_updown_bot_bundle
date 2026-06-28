from datetime import date

import pytest

from strategies import kalshi_optimize


def _patch_index_vol(monkeypatch, vol_prob):
    monkeypatch.setattr(
        kalshi_optimize,
        "parse_kalshi_index_ticker",
        lambda ticker: {
            "prefix": "KXINX",
            "strike": 7500.0,
            "expiry_date": date(2026, 6, 29),
            "direction": "above",
        },
    )
    monkeypatch.setattr(
        kalshi_optimize,
        "compute_vol_probability",
        lambda prefix, strike, days_to_expiry, direction: {
            "vol_prob": vol_prob,
            "current_price": 7400.0,
            "realized_vol_10d": 0.12,
            "model": "log_normal_vol",
        },
    )


def test_vol_gate_rejects_low_prob(monkeypatch):
    monkeypatch.setenv("MIN_VOL_PROB", "0.30")
    _patch_index_vol(monkeypatch, vol_prob=0.15)
    market = {"ticker": "KXINXU-26JUN29H1600-T7500", "_days_to_end": 1.0}

    adjusted = kalshi_optimize._apply_vol_model_or_shrinkage(market, 0.70)

    assert adjusted == pytest.approx(0.5)
    assert market["_vol_gate_rejected"] is True
    assert market["_vol_gate_min"] == pytest.approx(0.30)


def test_vol_gate_passes_high_prob(monkeypatch):
    monkeypatch.setenv("MIN_VOL_PROB", "0.30")
    _patch_index_vol(monkeypatch, vol_prob=0.45)
    market = {"ticker": "KXINXU-26JUN29H1600-T7500", "_days_to_end": 1.0}

    adjusted = kalshi_optimize._apply_vol_model_or_shrinkage(market, 0.70)

    assert adjusted == pytest.approx(0.55)
    assert market["_vol_gate_rejected"] is False


def test_vol_gate_passes_at_threshold(monkeypatch):
    monkeypatch.setenv("MIN_VOL_PROB", "0.30")
    _patch_index_vol(monkeypatch, vol_prob=0.30)
    market = {"ticker": "KXINXU-26JUN29H1600-T7500", "_days_to_end": 1.0}

    adjusted = kalshi_optimize._apply_vol_model_or_shrinkage(market, 0.70)

    assert adjusted == pytest.approx(0.46)
    assert market["_vol_gate_rejected"] is False


def test_vol_gate_skipped_non_index(monkeypatch):
    monkeypatch.setenv("MIN_VOL_PROB", "0.30")
    monkeypatch.setattr(kalshi_optimize, "parse_kalshi_index_ticker", lambda ticker: None)
    market = {"ticker": "KXHIGHNY-26JUN29-T85", "_days_to_end": 1.0}

    adjusted = kalshi_optimize._apply_vol_model_or_shrinkage(market, 0.80)

    assert adjusted == pytest.approx(0.65)
    assert market["_vol_gate_rejected"] is False
    assert market["_vol_model_prob"] is None


def test_vol_gate_disabled(monkeypatch):
    monkeypatch.setenv("MIN_VOL_PROB", "0.0")
    _patch_index_vol(monkeypatch, vol_prob=0.15)
    market = {"ticker": "KXINXU-26JUN29H1600-T7500", "_days_to_end": 1.0}

    adjusted = kalshi_optimize._apply_vol_model_or_shrinkage(market, 0.70)

    assert adjusted == pytest.approx(0.37)
    assert market["_vol_gate_rejected"] is False


def test_vol_gate_env_override(monkeypatch):
    monkeypatch.setenv("MIN_VOL_PROB", "0.40")
    _patch_index_vol(monkeypatch, vol_prob=0.35)
    market = {"ticker": "KXINXU-26JUN29H1600-T7500", "_days_to_end": 1.0}

    adjusted = kalshi_optimize._apply_vol_model_or_shrinkage(market, 0.70)

    assert adjusted == pytest.approx(0.5)
    assert market["_vol_gate_rejected"] is True
    assert market["_vol_gate_min"] == pytest.approx(0.40)
