#!/usr/bin/env python3
"""
Position Sizer Module

Comprehensive position sizing with:
- Bayesian probability estimation (Beta distribution)
- Fractional Kelly criterion with hard caps
- EV filtering (minimum edge threshold)
- Circuit breaker (drawdown protection)
- Position sizing pipeline

Author: Claude Code
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

# AI sentiment scorer available: from strategies.sentiment_scorer import get_bayesian_prior

# Configure logging
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

BASE_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
DATA_DIR = BASE_DIR / "data"
BAYESIAN_STATE_FILE = DATA_DIR / "bayesian_state.json"
CIRCUIT_BREAKER_FILE = DATA_DIR / "circuit_breaker.json"

# Default configuration (can be overridden by .env)
DEFAULT_CONFIG = {
    "kelly_fraction": 0.20,          # Fractional Kelly (20% = 0.2)
    "max_position_pct": 0.05,         # Max 5% of bankroll per position
    "min_position_usd": 0.01,        # Minimum $0.01 position (was 1.00)
    "max_concurrent_positions": 10,  # Max open positions
    "min_edge_threshold": 0.0,       # Min 0% edge to trade (was 0.05)
    "max_drawdown_pct": 0.30,        # Halt at 30% drawdown
}

# Load configuration from environment
def _load_config() -> dict:
    """Load configuration from environment variables"""
    config = DEFAULT_CONFIG.copy()

    # Load from .env if available
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")

    # Override with environment variables
    if val := os.getenv("KELLY_FRACTION"):
        config["kelly_fraction"] = float(val)
    if val := os.getenv("MAX_POSITION_PCT"):
        config["max_position_pct"] = float(val)
    if val := os.getenv("MIN_POSITION_USD"):
        config["min_position_usd"] = float(val)
    if val := os.getenv("MAX_CONCURRENT_POSITIONS"):
        config["max_concurrent_positions"] = int(val)
    if val := os.getenv("MIN_EDGE_THRESHOLD"):
        config["min_edge_threshold"] = float(val)
    if val := os.getenv("MAX_DRAWDOWN_PCT"):
        config["max_drawdown_pct"] = float(val)

    return config

CONFIG = _load_config()

# ============================================================================
# Bayesian Probability Estimation (Beta Distribution)
# ============================================================================

@dataclass
class BayesianEstimate:
    """
    Beta distribution tracker for market probability estimation.

    Tracks wins/losses to build posterior probability estimate.
    Beta(alpha, beta) where:
    - alpha = wins + 1 (successes + prior)
    - beta = losses + 1 (failures + prior)
    """
    market_id: str
    alpha: float = 1.0   # successes + 1 (prior)
    beta: float = 1.0    # failures + 1 (prior)
    observations: int = 0

    @property
    def mean(self) -> float:
        """Expected value (mean of Beta distribution)"""
        return self.alpha / (self.alpha + self.beta)

    @property
    def std(self) -> float:
        """Standard deviation of Beta distribution"""
        if self.alpha + self.beta <= 2:
            return 0.5  # High uncertainty with few observations
        num = self.alpha * self.beta
        den = (self.alpha + self.beta) ** 2 * (self.alpha + self.beta + 1)
        return math.sqrt(num / den) if den > 0 else 0.5

    @property
    def confidence(self) -> float:
        """Confidence level (1 - std)"""
        return max(0, min(1, 1 - self.std * 2))

    def update(self, outcome: bool) -> None:
        """
        Update estimate with new outcome.

        Args:
            outcome: True if market resolved YES/won, False if NO/lost
        """
        if outcome:
            self.alpha += 1
        else:
            self.beta += 1
        self.observations += 1
        logger.debug(f"BayesianUpdate({self.market_id}): alpha={self.alpha}, beta={self.beta}, mean={self.mean:.4f}")

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "alpha": self.alpha,
            "beta": self.beta,
            "observations": self.observations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BayesianEstimate":
        return cls(
            market_id=data["market_id"],
            alpha=data.get("alpha", 1.0),
            beta=data.get("beta", 1.0),
            observations=data.get("observations", 0),
        )


# ============================================================================
# Bayesian Tracker (Persistence)
# ============================================================================

class BayesianTracker:
    """
    Persists Bayesian probability estimates to disk.

    Tracks multiple markets and updates their probability estimates
    based on resolution outcomes.
    """

    def __init__(self, state_file: Path = BAYESIAN_STATE_FILE):
        self.state_file = state_file
        self.estimates: dict[str, BayesianEstimate] = {}
        self._load()

    def _load(self) -> None:
        """Load state from disk"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    for market_id, est_data in data.items():
                        self.estimates[market_id] = BayesianEstimate.from_dict(est_data)
                logger.info(f"Loaded {len(self.estimates)} Bayesian estimates from disk")
            except Exception as e:
                logger.warning(f"Failed to load Bayesian state: {e}")

    def _save(self) -> None:
        """Save state to disk"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {mid: est.to_dict() for mid, est in self.estimates.items()}
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save Bayesian state: {e}")

    def get_estimate(self, market_id: str, market_data: dict = None) -> BayesianEstimate:
        """Get or create estimate for a market.

        Args:
            market_id: Unique market identifier
            market_data: Optional dict with market info (title, current_price, etc.)
                       If provided, attempts to use AI sentiment scorer for prior.
        """
        if market_id not in self.estimates:
            # Try to get AI prior from sentiment scorer
            prior = 0.50
            prior_source = "uniform"

            if market_data:
                try:
                    from strategies.sentiment_scorer import get_bayesian_prior
                    ai_prior = get_bayesian_prior(market_data)
                    if ai_prior and 0.01 <= ai_prior <= 0.99:
                        prior = ai_prior
                        prior_source = "ai"
                        logger.info(f"[kelly] AI prior: {prior:.2f} for {market_id}")
                except Exception as e:
                    logger.warning(f"[kelly] Sentiment scorer failed, using flat prior: {e}")

            # Convert prior probability to Beta parameters
            # Use weight=4 to represent moderate confidence (~4 observations)
            prior_weight = 4.0
            alpha = prior * prior_weight
            beta = (1.0 - prior) * prior_weight

            self.estimates[market_id] = BayesianEstimate(
                market_id=market_id,
                alpha=alpha,
                beta=beta,
            )
            logger.debug(f"[kelly] Created BayesianEstimate for {market_id}: prior={prior:.2f} ({prior_source}), alpha={alpha:.2f}, beta={beta:.2f}")

        return self.estimates[market_id]

    def update_market(self, market_id: str, outcome: bool) -> None:
        """
        Update market probability with resolution outcome.

        Args:
            market_id: Unique market identifier
            outcome: True if YES/won, False if NO/lost
        """
        estimate = self.get_estimate(market_id)
        estimate.update(outcome)
        self._save()
        logger.info(f"BayesianUpdate({market_id}): outcome={outcome}, new_mean={estimate.mean:.4f}")

    def get_probability(self, market_id: str, market_data: dict = None) -> float:
        """Get current probability estimate for a market"""
        return self.get_estimate(market_id, market_data).mean

    def get_confidence(self, market_id: str, market_data: dict = None) -> float:
        """Get confidence level for a market estimate"""
        return self.get_estimate(market_id, market_data).confidence


# Global tracker instance
_bayesian_tracker: Optional[BayesianTracker] = None


def get_bayesian_tracker() -> BayesianTracker:
    """Get or create global Bayesian tracker instance"""
    global _bayesian_tracker
    if _bayesian_tracker is None:
        _bayesian_tracker = BayesianTracker()
    return _bayesian_tracker


# ============================================================================
# Kelly Criterion Calculator
# ============================================================================

def kelly_bet_size(
    win_prob: float,
    odds: float,
    kelly_fraction: float = None,
    max_position_pct: float = None,
    bankroll: float = 100.0,
) -> Tuple[float, dict]:
    """
    Calculate position size using Fractional Kelly criterion.

    Formula: f* = (bp - q) / b
        where:
        - b = odds - 1 (decimal odds - 1)
        - p = probability of winning
        - q = probability of losing = 1 - p
        - f* = fraction of bankroll to bet

    Args:
        win_prob: Probability of winning (0-1)
        odds: Decimal odds (e.g., 2.0 for even money)
        kelly_fraction: Fraction of Kelly to use (default from config)
        max_position_pct: Max position as % of bankroll (default from config)
        bankroll: Current bankroll size

    Returns:
        Tuple of (position_size, metadata)
    """
    # Use defaults from config
    if kelly_fraction is None:
        kelly_fraction = CONFIG["kelly_fraction"]
    if max_position_pct is None:
        max_position_pct = CONFIG["max_position_pct"]

    metadata = {
        "win_prob": win_prob,
        "odds": odds,
        "kelly_fraction": kelly_fraction,
        "bankroll": bankroll,
    }

    # Edge calculation
    loss_prob = 1.0 - win_prob
    b = odds - 1  # Net odds

    # Kelly formula: f* = (bp - q) / b
    if b <= 0:
        metadata["error"] = "Invalid odds (must be > 1)"
        return 0.0, metadata

    kelly_full = (b * win_prob - loss_prob) / b

    # Apply fractional Kelly
    kelly_size = kelly_fraction * kelly_full

    # Convert to dollar amount
    position_size = kelly_size * bankroll

    # Apply hard caps
    max_position = max_position_pct * bankroll
    min_position = CONFIG["min_position_usd"]

    # Cap at maximum
    position_size = min(position_size, max_position)

    # Floor at minimum (or zero if negative Kelly)
    if position_size < min_position:
        position_size = 0.0

    metadata.update({
        "kelly_full": kelly_full,
        "full_kelly_pct": kelly_full,  # Alias for diagnostic compatibility
        "kelly_size": kelly_size,
        "position_raw": kelly_size * bankroll,
        "position_capped": position_size,
        "max_position": max_position,
        "min_position": min_position,
        "capped": position_size < kelly_size * bankroll,
    })

    return position_size, metadata


# ============================================================================
# EV Filter
# ============================================================================

def passes_ev_filter(
    estimated_prob: float,
    market_price: float,
    min_edge_threshold: float = None,
    fees_pct: float = 0.0,
) -> Tuple[bool, float, float]:
    """
    Check if trade passes EV (Expected Value) filter.

    EV = (win_prob * payout) - (loss_prob * stake)
    Edge = EV / stake

    Args:
        estimated_prob: Our estimated true probability (0-1)
        market_price: Current market price (0-1, e.g., 0.55 for 55%)
        min_edge_threshold: Minimum edge required (default from config)
        fees_pct: Fees as percentage (0-1)

    Returns:
        Tuple of (passes_filter, ev, edge)
    """
    if min_edge_threshold is None:
        min_edge_threshold = CONFIG["min_edge_threshold"]

    # Calculate raw edge
    edge = estimated_prob - market_price

    # Adjust for fees
    edge_after_fees = edge - fees_pct

    # Check if passes threshold
    passes = edge_after_fees >= min_edge_threshold

    # EV is the edge-adjusted value (same as edge_after_fees)
    ev = edge_after_fees

    if not passes:
        logger.debug(f"EV filter: Edge {edge_after_fees:.2%} < threshold {min_edge_threshold:.2%}")

    return passes, ev, edge_after_fees


# ============================================================================
# Circuit Breaker (Drawdown Protection)
# ============================================================================

@dataclass
class CircuitBreakerState:
    """Tracks circuit breaker state for drawdown protection"""
    peak_bankroll: float = 100.0
    current_bankroll: float = 100.0
    halt_triggered: bool = False
    trades_since_halt: int = 0


class CircuitBreaker:
    """
    Circuit breaker that halts trading when drawdown exceeds threshold.

    Triggers halt when: (peak - current) / peak >= max_drawdown_pct
    """

    def __init__(
        self,
        state_file: Path = CIRCUIT_BREAKER_FILE,
        max_drawdown_pct: float = None,
    ):
        self.state_file = state_file
        self.max_drawdown_pct = max_drawdown_pct or CONFIG["max_drawdown_pct"]
        self.state = CircuitBreakerState()
        self._load()

    def _load(self) -> None:
        """Load state from disk"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.state = CircuitBreakerState(
                        peak_bankroll=data.get("peak_bankroll", 100.0),
                        current_bankroll=data.get("current_bankroll", 100.0),
                        halt_triggered=data.get("halt_triggered", False),
                        trades_since_halt=data.get("trades_since_halt", 0),
                    )
                logger.info(f"Circuit breaker loaded: peak=${self.state.peak_bankroll:.2f}, current=${self.state.current_bankroll:.2f}, halted={self.state.halt_triggered}")
            except Exception as e:
                logger.warning(f"Failed to load circuit breaker state: {e}")

    def _save(self) -> None:
        """Save state to disk"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump({
                    "peak_bankroll": self.state.peak_bankroll,
                    "current_bankroll": self.state.current_bankroll,
                    "halt_triggered": self.state.halt_triggered,
                    "trades_since_halt": self.state.trades_since_halt,
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save circuit breaker state: {e}")

    def update(self, current_bankroll: float) -> None:
        """
        Update with current bankroll and check for halt condition.

        Args:
            current_bankroll: Current account value
        """
        self.state.current_bankroll = current_bankroll

        # Update peak
        if current_bankroll > self.state.peak_bankroll:
            self.state.peak_bankroll = current_bankroll

        # Check drawdown
        if self.state.peak_bankroll > 0:
            drawdown = (self.state.peak_bankroll - current_bankroll) / self.state.peak_bankroll

            if drawdown >= self.max_drawdown_pct:
                if not self.state.halt_triggered:
                    logger.warning(f"CIRCUIT BREAKER TRIGGERED: Drawdown {drawdown:.2%} >= {self.max_drawdown_pct:.2%}")
                    self.state.halt_triggered = True

            # Auto-reset if recovered
            if self.state.halt_triggered and drawdown < self.max_drawdown_pct * 0.5:
                logger.info(f"CIRCUIT BREAKER RESET: Drawdown {drawdown:.2%} < {self.max_drawdown_pct * 0.5:.2%}")
                self.state.halt_triggered = False
                self.state.trades_since_halt = 0

        self._save()

    def can_trade(self) -> Tuple[bool, dict]:
        """
        Check if trading is allowed.

        Returns:
            Tuple of (can_trade, metadata)
        """
        metadata = {
            "peak_bankroll": self.state.peak_bankroll,
            "current_bankroll": self.state.current_bankroll,
            "max_drawdown_pct": self.max_drawdown_pct,
        }

        if self.state.peak_bankroll > 0:
            drawdown = (self.state.peak_bankroll - self.state.current_bankroll) / self.state.peak_bankroll
            metadata["current_drawdown_pct"] = drawdown
        else:
            metadata["current_drawdown_pct"] = 0.0

        metadata["halt_triggered"] = self.state.halt_triggered

        can_trade = not self.state.halt_triggered

        if not can_trade:
            metadata["reason"] = "Circuit breaker halted due to drawdown"

        return can_trade, metadata

    def record_trade(self) -> None:
        """Record that a trade was executed"""
        self.state.trades_since_halt += 1
        self._save()

    def reset(self) -> None:
        """Manually reset the circuit breaker"""
        self.state = CircuitBreakerState()
        self._save()
        logger.info("Circuit breaker manually reset")


# Global circuit breaker instance
_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get or create global circuit breaker instance"""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


# ============================================================================
# Main Position Sizing Pipeline
# ============================================================================

def size_position(
    market_id: str,
    market_price: float,
    bankroll: float,
    current_positions: int = 0,
    estimated_prob: float = None,
    odds: float = None,
    fees_pct: float = 0.0,
    kelly_fraction: float = None,
    max_position_pct: float = None,
    update_circuit_breaker: bool = True,
    market_data: dict = None,
) -> Tuple[float, dict]:
    """
    Main entry point for position sizing.

    Full pipeline:
    1. Check circuit breaker (drawdown protection)
    2. Check concurrent position limit
    3. Check EV filter (minimum edge)
    4. Calculate Kelly position size
    5. Apply hard caps

    Args:
        market_id: Unique market identifier
        market_price: Current market price (0-1)
        bankroll: Current bankroll size
        current_positions: Number of currently open positions
        estimated_prob: Our estimated probability (defaults to market_price or Bayesian)
        odds: Decimal odds (defaults to 1/market_price)
        fees_pct: Fees as percentage
        kelly_fraction: Fraction of Kelly to use
        max_position_pct: Max position as % of bankroll
        update_circuit_breaker: Whether to update circuit breaker state
        market_data: Optional dict with market info (title, description, etc.) for AI prior

    Returns:
        Tuple of (position_size, metadata)
    """
    metadata = {
        "market_id": market_id,
        "market_price": market_price,
        "bankroll": bankroll,
        "current_positions": current_positions,
    }

    # Get configuration
    config = CONFIG.copy()
    if kelly_fraction is not None:
        config["kelly_fraction"] = kelly_fraction
    if max_position_pct is not None:
        config["max_position_pct"] = max_position_pct

    # 1. Check circuit breaker
    if update_circuit_breaker:
        circuit_breaker = get_circuit_breaker()
        can_trade, cb_meta = circuit_breaker.can_trade()
        metadata["circuit_breaker"] = cb_meta
        if not can_trade:
            metadata["blocked"] = True
            metadata["block_reason"] = "Circuit breaker halted"
            return 0.0, metadata

    # 2. Check concurrent position limit
    if current_positions >= config["max_concurrent_positions"]:
        metadata["blocked"] = True
        metadata["block_reason"] = f"Max positions ({config['max_concurrent_positions']}) reached"
        return 0.0, metadata

    # 3. Get probability estimate
    if estimated_prob is None:
        # Try Bayesian first, fall back to market price
        try:
            tracker = get_bayesian_tracker()
            estimated_prob = tracker.get_probability(market_id, market_data)
            metadata["prob_source"] = "bayesian"
        except Exception as e:
            logger.warning(f"[kelly] Bayesian tracker failed: {e}, using market price")
            estimated_prob = market_price
            metadata["prob_source"] = "market_price"

    metadata["estimated_prob"] = estimated_prob

    # 4. Check EV filter
    passes_ev, ev_value, edge_value = passes_ev_filter(
        market_price=market_price,
        estimated_prob=estimated_prob,
        fees_pct=fees_pct,
    )
    metadata["ev_filter"] = {
        "market_price": market_price,
        "estimated_prob": estimated_prob,
        "ev": ev_value,
        "edge": edge_value,
        "passes": passes_ev,
    }
    if not passes_ev:
        metadata["blocked"] = True
        metadata["block_reason"] = f"EV filter failed: edge {edge_value:.2%} below threshold"
        return 0.0, metadata

    # 5. Calculate Kelly size
    if odds is None:
        odds = 1.0 / market_price if market_price > 0 else 1.0

    # Use Bayesian confidence to adjust Kelly fraction
    # Note: confidence starts at 0 with no observations (high uncertainty)
    # We apply a floor of 0.25 so new markets still get some Kelly
    try:
        tracker = get_bayesian_tracker()
        confidence = tracker.get_confidence(market_id)
        # Floor confidence at 0.25 to allow new markets to trade
        confidence = max(confidence, 0.25)
        adjusted_kelly = config["kelly_fraction"] * confidence
    except Exception:
        adjusted_kelly = config["kelly_fraction"]

    position_size, kelly_meta = kelly_bet_size(
        win_prob=estimated_prob,
        odds=odds,
        kelly_fraction=adjusted_kelly,
        max_position_pct=config["max_position_pct"],
        bankroll=bankroll,
    )
    metadata["kelly"] = kelly_meta

    # 6. Final validation
    if position_size < config["min_position_usd"]:
        metadata["blocked"] = True
        metadata["block_reason"] = f"Position size ${position_size:.2f} < min ${config['min_position_usd']:.2f}"
        return 0.0, metadata

    metadata["position_size"] = position_size
    metadata["blocked"] = False

    logger.info(f"size_position({market_id}): ${position_size:.2f} (bankroll: ${bankroll:.2f}, prob: {estimated_prob:.2%}, price: {market_price:.2%})")

    return position_size, metadata


# ============================================================================
# Convenience Functions
# ============================================================================

def update_bankroll(bankroll: float) -> None:
    """Update circuit breaker with current bankroll"""
    circuit_breaker = get_circuit_breaker()
    circuit_breaker.update(bankroll)


def record_trade() -> None:
    """Record that a trade was executed"""
    circuit_breaker = get_circuit_breaker()
    circuit_breaker.record_trade()


def reset_circuit_breaker() -> None:
    """Reset circuit breaker state"""
    circuit_breaker = get_circuit_breaker()
    circuit_breaker.reset()


def get_position_limits(bankroll: float) -> dict:
    """Get current position limits based on bankroll"""
    return {
        "max_position_usd": CONFIG["max_position_pct"] * bankroll,
        "min_position_usd": CONFIG["min_position_usd"],
        "max_positions": CONFIG["max_concurrent_positions"],
    }
