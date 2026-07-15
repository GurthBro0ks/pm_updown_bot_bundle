from __future__ import annotations

import math

import pytest

from research.candidate_ledger.models import ValidationError
from research.candidate_ledger.replay import (
    brier_score,
    calibration_bucket,
    clipped_log_loss,
    entry_price_dollars,
    equity_curve,
    expected_calibration_error,
    fee_for_assumption,
    hypothetical_pnl,
    maximum_drawdown,
    no_payoff,
    no_trade_baseline,
    realized_pnl,
    strategy_version_comparison,
    trade_pnl,
    yes_payoff,
)


def test_yes_and_no_payoffs_and_entry_price():
    assert yes_payoff("yes") == 1.0
    assert yes_payoff("no") == 0.0
    assert no_payoff("no") == 1.0
    assert no_payoff("yes") == 0.0
    assert entry_price_dollars(63) == 0.63


def test_filled_partial_hypothetical_and_no_trade_pnl():
    assert trade_pnl(side="yes", settlement_result="yes", entry_price_cents=60, quantity=2, fees=0.1) == pytest.approx(0.7)
    assert realized_pnl(side="no", settlement_result="no", average_fill_price_cents=40, filled_quantity=1, fees=0.02) == pytest.approx(0.58)
    assert hypothetical_pnl(side="yes", settlement_result="no", entry_price_cents=25, hypothetical_quantity=3, fees=0.03) == pytest.approx(-0.78)
    assert no_trade_baseline() == 0.0


def test_unknown_fee_remains_unknown():
    assert trade_pnl(side="yes", settlement_result="yes", entry_price_cents=60, quantity=2, fees=None) is None
    assert hypothetical_pnl(side="no", settlement_result="no", entry_price_cents=40, hypothetical_quantity=1, fees=None) is None


def test_maker_and_taker_fees_are_explicit_and_never_defaulted():
    assert fee_for_assumption(maker_assumption="maker", maker_fee=0.01, taker_fee=0.05) == 0.01
    assert fee_for_assumption(maker_assumption="taker", maker_fee=0.01, taker_fee=0.05) == 0.05
    assert fee_for_assumption(maker_assumption="maker", maker_fee=None, taker_fee=0.05) is None
    assert fee_for_assumption(maker_assumption="unknown", maker_fee=0.01, taker_fee=0.05) is None


def test_brier_clipped_log_loss_and_calibration():
    assert brier_score(0.8, "yes") == pytest.approx(0.04)
    assert brier_score(0.8, "no") == pytest.approx(0.64)
    assert clipped_log_loss(0.8, "yes") == pytest.approx(-math.log(0.8))
    assert math.isfinite(clipped_log_loss(1.0, "no"))
    assert calibration_bucket(0.0) == "0.0-0.1"
    assert calibration_bucket(0.75) == "0.7-0.8"
    assert calibration_bucket(1.0) == "0.9-1.0"
    assert expected_calibration_error([(0.8, "yes"), (0.2, "no")]) == pytest.approx(0.2)


def test_equity_curve_drawdown_and_strategy_comparison_are_deterministic():
    curve = equity_curve([1.0, -0.5, 2.0])
    assert curve == [0.0, 1.0, 0.5, 2.5]
    assert maximum_drawdown(curve) == 0.5
    observations = [
        {"strategy_version": "v2", "probability_yes": 0.8, "settlement_result": "yes", "pnl": 0.3},
        {"strategy_version": "v1", "probability_yes": 0.3, "settlement_result": "no", "pnl": None},
        {"strategy_version": "v2", "probability_yes": 0.4, "settlement_result": "no", "pnl": -0.1},
    ]
    first = strategy_version_comparison(observations)
    second = strategy_version_comparison(reversed(observations))
    assert first == second
    assert list(first) == ["v1", "v2"]
    assert first["v2"]["candidate_count"] == 2
    assert first["v2"]["total_pnl"] == pytest.approx(0.2)
    assert first["v1"]["known_pnl_count"] == 0


def test_replay_rejects_invalid_ranges():
    with pytest.raises(ValidationError):
        entry_price_dollars(101)
    with pytest.raises(ValidationError):
        brier_score(-0.1, "yes")
    with pytest.raises(ValidationError):
        trade_pnl(side="yes", settlement_result="yes", entry_price_cents=50, quantity=-1, fees=0)
