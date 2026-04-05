"""Tests for strategies/contract_signals.py"""

import sys
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

import pytest
from strategies.contract_signals import (
    compute_momentum,
    compute_zscore,
    volume_confidence,
    expiry_confidence,
    validate_prior,
)


# --- compute_momentum ---


def test_momentum_positive():
    prices = [0.50, 0.51, 0.52, 0.53, 0.54, 0.55]
    assert compute_momentum(prices, window=5) == pytest.approx(0.55 - 0.51)


def test_momentum_negative():
    prices = [0.60, 0.58, 0.56, 0.54, 0.52, 0.50]
    assert compute_momentum(prices, window=5) == pytest.approx(0.50 - 0.58)


def test_momentum_insufficient_data():
    prices = [0.50, 0.51]
    assert compute_momentum(prices, window=5) is None
    assert compute_momentum([], window=5) is None


# --- compute_zscore ---


def test_zscore_normal():
    # [0.40, 0.41, 0.42, 0.43, 0.44, 0.45, 0.46, 0.47, 0.48, 0.49]
    # mean = 0.445, stdev ~ 0.0303, current = 0.49
    prices = [0.40, 0.41, 0.42, 0.43, 0.44, 0.45, 0.46, 0.47, 0.48, 0.49]
    zsc = compute_zscore(prices, window=10)
    assert zsc is not None
    assert 1.0 < zsc < 2.0  # positive z, within normal range


def test_zscore_extreme_low():
    # Most prices near 0.50, but current is 0.01
    prices = [0.49, 0.50, 0.49, 0.50, 0.49, 0.50, 0.49, 0.50, 0.49, 0.01]
    zsc = compute_zscore(prices, window=10)
    assert zsc is not None
    assert zsc < -2.0  # extreme low


def test_zscore_insufficient_data():
    assert compute_zscore([0.50, 0.51], window=10) is None
    assert compute_zscore([], window=10) is None


def test_zscore_near_zero_std():
    # All identical prices -> zero std -> None
    prices = [0.50] * 10
    assert compute_zscore(prices, window=10) is None


# --- volume_confidence ---


def test_volume_confidence_zero():
    # volume=0 -> ratio=0 -> 0.3 + 0.7*0/(1+0) = 0.3
    assert volume_confidence(0) == pytest.approx(0.3)


def test_volume_confidence_high():
    # volume=1000, median=500 -> ratio=2 -> 0.3 + 0.7*2/3 = 0.3 + 0.4667 = 0.7667
    conf = volume_confidence(1000, median_volume=500)
    assert 0.7 < conf < 0.8


def test_volume_confidence_unity():
    # Very high volume -> approaches 1.0
    conf = volume_confidence(1_000_000, median_volume=500)
    assert conf > 0.99


# --- expiry_confidence ---


def test_expiry_confidence_values():
    assert expiry_confidence(0.5) == 1.0    # < 1h
    assert expiry_confidence(0.99) == 1.0
    assert expiry_confidence(1.0) == 0.9    # < 24h
    assert expiry_confidence(12) == 0.9
    assert expiry_confidence(23.9) == 0.9
    assert expiry_confidence(24.0) == 0.7    # < 72h
    assert expiry_confidence(71.9) == 0.7
    assert expiry_confidence(72.0) == 0.5    # else
    assert expiry_confidence(168) == 0.5    # 1 week


# --- validate_prior ---


def test_validate_prior_ok():
    # Normal prior with no red flags
    result = validate_prior(
        prior=0.65,
        momentum=0.05,
        zscore=0.3,
        contract_price=0.60,
    )
    assert result["passed"] is True
    assert result["confidence"] == 1.0
    assert result["flags"] == []
    assert result["adjusted_prior"] == 0.65


def test_validate_prior_degenerate():
    # Prior < 0.01 gets clamped to 0.05; confidence ×0.5 → 0.5 (still >= 0.4, passes alone)
    # Use momentum conflict to also reduce confidence so it fails the 0.4 threshold
    # prior=0.005 -> clamped to 0.05 (bearish since <0.5), momentum=0.10 (positive) -> conflict
    result = validate_prior(prior=0.005, momentum=0.10, zscore=None, contract_price=None)
    assert result["passed"] is False
    assert "degenerate_prior" in result["flags"]
    assert "momentum_conflict" in result["flags"]
    assert result["adjusted_prior"] == 0.05
    # 1.0 × 0.5 × 0.7 = 0.35 < 0.4
    assert result["confidence"] < 0.4

    # Prior > 0.99 gets clamped
    result2 = validate_prior(prior=0.995, momentum=None, zscore=None, contract_price=None)
    assert result2["passed"] is True  # confidence = 0.5, just barely passes
    assert "degenerate_prior" in result2["flags"]
    assert result2["adjusted_prior"] == 0.95


def test_validate_prior_momentum_conflict():
    # Prior bullish (0.7) but momentum negative -> ×0.7
    result = validate_prior(
        prior=0.70,
        momentum=-0.10,
        zscore=None,
        contract_price=0.65,
    )
    assert "momentum_conflict" in result["flags"]
    assert result["confidence"] == pytest.approx(0.7)

    # Prior bearish (0.3) but momentum positive -> conflict
    result2 = validate_prior(
        prior=0.30,
        momentum=0.10,
        zscore=None,
        contract_price=0.35,
    )
    assert "momentum_conflict" in result2["flags"]
    assert result2["confidence"] == pytest.approx(0.7)


def test_validate_prior_high_divergence():
    # Prior 0.90, market 0.30 -> divergence 0.60 > 0.30 -> dampen 70/30
    result = validate_prior(
        prior=0.90,
        momentum=None,
        zscore=None,
        contract_price=0.30,
    )
    assert "market_divergence" in result["flags"]
    expected = 0.7 * 0.90 + 0.3 * 0.30
    assert result["adjusted_prior"] == pytest.approx(expected)


def test_validate_prior_confidence_threshold():
    # Multiple flags multiply confidence:
    # degenerate_prior × momentum_conflict × extreme_zscore
    # 0.5 × 0.7 × 0.8 = 0.28 < 0.4 -> fail
    # prior=0.005 -> clamped to 0.05 (bearish since <0.5), momentum=0.10 (positive) -> conflict
    result = validate_prior(
        prior=0.005,        # degenerate -> ×0.5
        momentum=0.10,      # positive momentum but prior is bearish -> conflict -> ×0.7
        zscore=3.0,         # extreme -> ×0.8
        contract_price=0.40,
    )
    assert result["passed"] is False
    assert "degenerate_prior" in result["flags"]
    assert "momentum_conflict" in result["flags"]
    assert "extreme_zscore" in result["flags"]
    assert result["confidence"] < 0.4
    assert result["confidence"] < 0.4


def test_validate_prior_extreme_zscore():
    result = validate_prior(
        prior=0.55,
        momentum=None,
        zscore=2.5,
        contract_price=None,
    )
    assert "extreme_zscore" in result["flags"]
    assert result["confidence"] == pytest.approx(0.8)


def test_validate_prior_all_signals_none():
    # No optional signals -> only degenerate check applies
    result = validate_prior(prior=0.50, momentum=None, zscore=None, contract_price=None)
    assert result["passed"] is True
    assert result["confidence"] == 1.0
    assert result["flags"] == []
