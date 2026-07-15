"""Deterministic offline binary-contract replay calculations."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Iterable, Mapping, Sequence

from .models import ValidationError, canonical_json


def _decimal(value: int | float | Decimal, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ValidationError(f"{field} must be finite")
    return result


def entry_price_dollars(entry_price_cents: int | float) -> float:
    """Convert a bounded cents price to dollars per contract."""

    cents = _decimal(entry_price_cents, "entry_price_cents")
    if cents < 0 or cents > 100:
        raise ValidationError("entry_price_cents must be between 0 and 100")
    return float(cents / Decimal(100))


def binary_payoff(*, side: str, settlement_result: str) -> float:
    """Return the $1-or-$0 payoff for a YES or NO contract."""

    if side not in {"yes", "no"}:
        raise ValidationError("side must be yes or no")
    if settlement_result not in {"yes", "no"}:
        raise ValidationError("settlement_result must be yes or no")
    return 1.0 if side == settlement_result else 0.0


def yes_payoff(settlement_result: str) -> float:
    """Return the payoff for one YES contract."""

    return binary_payoff(side="yes", settlement_result=settlement_result)


def no_payoff(settlement_result: str) -> float:
    """Return the payoff for one NO contract."""

    return binary_payoff(side="no", settlement_result=settlement_result)


def trade_pnl(
    *,
    side: str,
    settlement_result: str,
    entry_price_cents: int | float,
    quantity: int,
    fees: int | float | None,
) -> float | None:
    """Return fee-aware PnL, or unknown when fees were not supplied."""

    if type(quantity) is not int or quantity < 0:
        raise ValidationError("quantity must be a non-negative integer")
    if fees is None:
        return None
    fee_value = _decimal(fees, "fees")
    if fee_value < 0:
        raise ValidationError("fees must be non-negative")
    payoff = _decimal(binary_payoff(side=side, settlement_result=settlement_result), "payoff")
    entry = _decimal(entry_price_dollars(entry_price_cents), "entry_price")
    pnl = Decimal(quantity) * (payoff - entry) - fee_value
    return float(pnl)


def realized_pnl(
    *,
    side: str,
    settlement_result: str,
    average_fill_price_cents: int | float,
    filled_quantity: int,
    fees: int | float | None,
) -> float | None:
    """PnL for an actually filled or partially filled quantity."""

    return trade_pnl(
        side=side,
        settlement_result=settlement_result,
        entry_price_cents=average_fill_price_cents,
        quantity=filled_quantity,
        fees=fees,
    )


def hypothetical_pnl(
    *,
    side: str,
    settlement_result: str,
    entry_price_cents: int | float,
    hypothetical_quantity: int,
    fees: int | float | None,
) -> float | None:
    """PnL for an unfilled or rejected candidate under explicit assumptions."""

    return trade_pnl(
        side=side,
        settlement_result=settlement_result,
        entry_price_cents=entry_price_cents,
        quantity=hypothetical_quantity,
        fees=fees,
    )


def no_trade_baseline() -> float:
    """Return the deterministic zero-PnL baseline."""

    return 0.0


def fee_for_assumption(
    *,
    maker_assumption: str,
    maker_fee: int | float | None,
    taker_fee: int | float | None,
) -> float | None:
    """Select an explicit maker/taker fee without supplying any default."""

    if maker_assumption not in {"maker", "taker", "unknown"}:
        raise ValidationError("maker_assumption must be maker, taker, or unknown")
    for value, field in ((maker_fee, "maker_fee"), (taker_fee, "taker_fee")):
        if value is not None and _decimal(value, field) < 0:
            raise ValidationError(f"{field} must be non-negative")
    if maker_assumption == "maker":
        return None if maker_fee is None else float(_decimal(maker_fee, "maker_fee"))
    if maker_assumption == "taker":
        return None if taker_fee is None else float(_decimal(taker_fee, "taker_fee"))
    return None


def brier_score(probability_yes: float, settlement_result: str) -> float:
    """Compute binary Brier score against a YES probability."""

    probability = _decimal(probability_yes, "probability_yes")
    if probability < 0 or probability > 1:
        raise ValidationError("probability_yes must be between 0 and 1")
    if settlement_result not in {"yes", "no"}:
        raise ValidationError("settlement_result must be yes or no")
    outcome = Decimal(1 if settlement_result == "yes" else 0)
    return float((probability - outcome) ** 2)


def clipped_log_loss(
    probability_yes: float,
    settlement_result: str,
    *,
    epsilon: float = 1e-15,
) -> float:
    """Compute natural-log loss with an explicit deterministic clip."""

    probability = float(_decimal(probability_yes, "probability_yes"))
    clip = float(_decimal(epsilon, "epsilon"))
    if not 0 < clip < 0.5:
        raise ValidationError("epsilon must be between 0 and 0.5")
    if not 0 <= probability <= 1:
        raise ValidationError("probability_yes must be between 0 and 1")
    if settlement_result not in {"yes", "no"}:
        raise ValidationError("settlement_result must be yes or no")
    p = min(max(probability, clip), 1.0 - clip)
    return -math.log(p if settlement_result == "yes" else 1.0 - p)


def calibration_bucket(probability_yes: float, *, bucket_count: int = 10) -> str:
    """Assign a stable left-closed probability bucket label."""

    if type(bucket_count) is not int or bucket_count <= 0 or bucket_count > 100:
        raise ValidationError("bucket_count must be an integer from 1 to 100")
    probability = float(_decimal(probability_yes, "probability_yes"))
    if not 0 <= probability <= 1:
        raise ValidationError("probability_yes must be between 0 and 1")
    index = min(int(probability * bucket_count), bucket_count - 1)
    lower = index / bucket_count
    upper = (index + 1) / bucket_count
    precision = max(1, len(str(bucket_count)) - 1)
    return f"{lower:.{precision}f}-{upper:.{precision}f}"


def expected_calibration_error(
    observations: Iterable[tuple[float, str]],
    *,
    bucket_count: int = 10,
) -> float:
    """Compute weighted absolute calibration error across non-empty buckets."""

    grouped: dict[str, list[tuple[float, int]]] = defaultdict(list)
    total = 0
    for probability, result in observations:
        if result not in {"yes", "no"}:
            raise ValidationError("settlement_result must be yes or no")
        bucket = calibration_bucket(probability, bucket_count=bucket_count)
        grouped[bucket].append((probability, 1 if result == "yes" else 0))
        total += 1
    if total == 0:
        raise ValidationError("expected calibration error requires observations")
    error = 0.0
    for bucket in sorted(grouped):
        values = grouped[bucket]
        mean_probability = sum(item[0] for item in values) / len(values)
        mean_outcome = sum(item[1] for item in values) / len(values)
        error += (len(values) / total) * abs(mean_probability - mean_outcome)
    return error


def equity_curve(pnls: Iterable[float], *, initial_equity: float = 0.0) -> list[float]:
    """Return cumulative equity including the initial point."""

    current = float(_decimal(initial_equity, "initial_equity"))
    curve = [current]
    for pnl in pnls:
        current += float(_decimal(pnl, "pnl"))
        curve.append(current)
    return curve


def maximum_drawdown(curve: Sequence[float]) -> float:
    """Return maximum peak-to-trough drawdown as a non-negative amount."""

    if not curve:
        raise ValidationError("maximum_drawdown requires an equity curve")
    peak = float(_decimal(curve[0], "curve[0]"))
    maximum = 0.0
    for raw in curve:
        value = float(_decimal(raw, "curve value"))
        peak = max(peak, value)
        maximum = max(maximum, peak - value)
    return maximum


def strategy_version_comparison(
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, float | int | None]]:
    """Return deterministic aggregate replay summaries by strategy version."""

    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observations:
        version = observation.get("strategy_version")
        if not isinstance(version, str) or not version:
            raise ValidationError("strategy_version is required")
        groups[version].append(observation)
    result: dict[str, dict[str, float | int | None]] = {}
    for version in sorted(groups):
        rows = sorted(
            groups[version],
            key=lambda row: (
                str(row.get("run_timestamp", "")),
                str(row.get("candidate_id", "")),
                canonical_json(row),
            ),
        )
        resolved = [row for row in rows if row.get("settlement_result") in {"yes", "no"}]
        scores = [brier_score(float(row["probability_yes"]), str(row["settlement_result"])) for row in resolved]
        losses = [clipped_log_loss(float(row["probability_yes"]), str(row["settlement_result"])) for row in resolved]
        known_pnls = [float(row["pnl"]) for row in rows if row.get("pnl") is not None]
        curve = equity_curve(known_pnls)
        result[version] = {
            "candidate_count": len(rows),
            "resolved_count": len(resolved),
            "known_pnl_count": len(known_pnls),
            "total_pnl": sum(known_pnls) if known_pnls else None,
            "mean_brier_score": sum(scores) / len(scores) if scores else None,
            "mean_log_loss": sum(losses) / len(losses) if losses else None,
            "expected_calibration_error": expected_calibration_error(
                [(float(row["probability_yes"]), str(row["settlement_result"])) for row in resolved]
            ) if resolved else None,
            "maximum_drawdown": maximum_drawdown(curve),
            "no_trade_total_pnl": 0.0,
        }
    return result
