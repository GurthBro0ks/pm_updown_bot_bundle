#!/usr/bin/env python3
"""
Kalshi Optimization Strategy - Phase 1 (Quick Wins)
Maker order logic, probability-weighted edge detection, trade frequency optimization
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Add to path
sys.path.insert(0, '/opt/slimy/pm_updown_bot_bundle')

# Local imports (avoid circular import)
from utils.proof import generate_proof
from utils.kalshi import fetch_kalshi_markets
from utils.pnl_database import record_trade
try:
    from utils.edge_nearest_miss import build_summary as build_edge_nearest_miss_summary
    from utils.edge_nearest_miss import (
        make_nearest_miss,
        public_count_string,
        write_summary as write_edge_nearest_miss_summary,
    )
except Exception:
    build_edge_nearest_miss_summary = None
    make_nearest_miss = None
    public_count_string = None
    write_edge_nearest_miss_summary = None

try:
    from utils.discord_notify import notify_order_placed
except Exception:
    notify_order_placed = None
try:
    from strategies.sentiment_scorer import get_ai_prior, was_last_prior_fallback as _was_last_prior_fallback
except Exception:
    get_ai_prior = None
    _was_last_prior_fallback = None

try:
    from providers.vol_model import (
        apply_probability_shrinkage,
        blend_probability,
        compute_vol_probability,
        parse_kalshi_index_ticker,
    )
except Exception:
    compute_vol_probability = None
    parse_kalshi_index_ticker = None

    def blend_probability(ai_prob: float, vol_prob: float) -> float:
        return max(0.0, min(1.0, (0.6 * float(vol_prob)) + (0.4 * float(ai_prob))))

    def apply_probability_shrinkage(ai_prob: float) -> float:
        return max(0.0, min(1.0, 0.5 + (float(ai_prob) - 0.5) * 0.5))

try:
    from providers.ai_calibration import (
        build_calibration_table,
        calibrate_probability,
        bucket_name as calibration_bucket_name,
        nearest_bucket_name,
    )
except Exception:
    build_calibration_table = None
    calibrate_probability = None
    calibration_bucket_name = None
    nearest_bucket_name = None

try:
    from core.market_cursor import rotate_markets as _rotate_markets, compute_list_hash as _compute_list_hash
except Exception:
    _rotate_markets = None
    _compute_list_hash = None

try:
    from strategies.contract_signals import (
        compute_momentum,
        compute_zscore,
        volume_confidence,
        expiry_confidence,
        validate_prior,
    )
except Exception:
    compute_momentum = None
    compute_zscore = None
    volume_confidence = None
    expiry_confidence = None
    validate_prior = None

try:
    from config import SHADOW_PER_CALL_TIMEOUT_CAP
except Exception:
    SHADOW_PER_CALL_TIMEOUT_CAP = 5.0

try:
    from research.candidate_ledger.runtime_adapter import create_candidate_capture
except Exception:
    create_candidate_capture = None

_TICKER_CATEGORY_MAP = {
    "KXINX": "index", "KXINXU": "index", "KXNDX": "index",
    "KXNASDAQ100": "index", "KXNASDAQ100U": "index",
    "KXBTC": "crypto", "KXETH": "crypto", "KXETHY": "crypto",
    "KXMVESPORTS": "sports", "KXBUNDESLIGA": "sports",
    "KXGOV": "politics", "KXECON": "economics",
    "KXCOACH": "sports", "KXNFL": "sports",
}

# Edge calculation sanity limits
MAX_EDGE_PCT = 500.0
MIN_EDGE_PCT = -100.0


def _record_shadow_candidate(capture_runtime, **evaluation):
    """Best-effort observation only; never return data to the decision path."""

    if capture_runtime is None:
        return
    try:
        capture_runtime.record_evaluation(**evaluation)
    except Exception:
        logger.warning("[SHADOW_CAPTURE] candidate observation dropped")


def _flush_shadow_candidates(capture_runtime):
    """Flush once and emit bounded status fields without propagating failures."""

    fallback = {
        "SHADOW_CAPTURE_ENABLED": "false",
        "SHADOW_CAPTURE_ATTEMPTED": "false",
        "SHADOW_CAPTURE_WRITTEN_COUNT": 0,
        "SHADOW_CAPTURE_DROPPED_COUNT": 0,
        "SHADOW_CAPTURE_WARNING_COUNT": 0,
        "SHADOW_CAPTURE_STATUS": "DISABLED",
    }
    if capture_runtime is not None:
        try:
            fallback = capture_runtime.flush().summary_fields()
        except Exception:
            fallback.update(
                {
                    "SHADOW_CAPTURE_ENABLED": "true",
                    "SHADOW_CAPTURE_WARNING_COUNT": 1,
                    "SHADOW_CAPTURE_STATUS": "WARN",
                }
            )
    for name, value in fallback.items():
        logger.info("[SHADOW_CAPTURE] %s=%s", name, value)
    return fallback

# Expiry filters (configurable via env)
MAX_DAYS_TO_EXPIRY = float(os.getenv("MAX_DAYS_TO_EXPIRY", "14") or "14")
MAX_LONG_TERM_DAYS = float(os.getenv("MAX_LONG_TERM_DAYS", "30") or "30")

# Category allowlist/blocklist (configurable via env)
DEFAULT_ALLOWED_CATEGORIES = {"index", "crypto", "economics", "commodities", "financials", "politics"}
_allowed_env = os.getenv("KALSHI_ALLOWED_CATEGORIES", "")
if _allowed_env.strip():
    ALLOWED_CATEGORIES = set(c.strip().lower() for c in _allowed_env.split(",") if c.strip())
else:
    ALLOWED_CATEGORIES = DEFAULT_ALLOWED_CATEGORIES

# Price floor (configurable via env)
MIN_TRADE_PRICE_CENTS = int(os.getenv("MIN_TRADE_PRICE_CENTS", "5") or "5")

# Daily loss guard (configurable via env)
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", "1.00") or "1.00")

# Run limits (configurable via env)
MAX_ORDERS_PER_RUN = int(os.getenv("MAX_ORDERS_PER_RUN", "2") or "2")
MAX_NOTIONAL_PER_RUN_USD = float(os.getenv("MAX_NOTIONAL_PER_RUN_USD", "1.00") or "1.00")

# Minimum vol model probability for index markets. Trades where the vol model
# says P(above strike) is below this value are rejected. Set to 0.0 to disable.
# Default 0.30 is based on backtest scenario F: 46.4% WR, $5.78 PnL, 0.17 Sharpe.
MIN_VOL_PROB = float(os.getenv("MIN_VOL_PROB", "0.30") or "0.30")


def calculate_edge_pct_with_flag(ai_prob: float, market_price: float, market_id: str = "?") -> tuple[float, bool]:
    """
    Calculate edge percentage with validation and sanity caps.

    Returns:
        (edge_pct: float, was_capped: bool)
        was_capped is True if raw edge exceeded MAX_EDGE_PCT.
    """
    # Validate inputs are not None
    if ai_prob is None or market_price is None:
        logger.debug("Edge calc skipped for %s: ai_prob=%s market_price=%s", market_id, ai_prob, market_price)
        return 0.0, False

    # Validate inputs are numeric
    try:
        ai_prob = float(ai_prob)
        market_price = float(market_price)
    except (TypeError, ValueError):
        logger.debug("Edge calc skipped for %s: non-numeric ai_prob=%s market_price=%s", market_id, ai_prob, market_price)
        return 0.0, False

    # Validate inputs are in 0.0-1.0 range
    if not (0.0 <= ai_prob <= 1.0):
        logger.warning("Edge calc: ai_prob %.4f out of range [0,1] for %s — capping to [0,1]", ai_prob, market_id)
        ai_prob = max(0.0, min(1.0, ai_prob))
    if not (0.0 < market_price <= 1.0):
        logger.debug("Edge calc skipped for %s: market_price %.4f out of range (0,1]", market_id, market_price)
        return 0.0, False

    # Calculate edge
    edge = ((ai_prob - market_price) / market_price) * 100.0

    # Sanity cap
    was_capped = False
    if edge > MAX_EDGE_PCT:
        logger.warning("Edge capped for %s: raw=%.2f%% -> capped=%.2f%%", market_id, edge, MAX_EDGE_PCT)
        was_capped = True
        edge = MAX_EDGE_PCT

    # Sanity floor: edge below -100% is nonsensical, skip
    if edge < MIN_EDGE_PCT:
        logger.warning("Edge rejected for %s: %.2f%% < %.2f%%", market_id, edge, MIN_EDGE_PCT)
        return 0.0, False

    return edge, was_capped


def calculate_edge_pct(ai_prob: float, market_price: float, market_id: str = "?") -> float:
    """
    Calculate edge percentage with validation and sanity caps.

    Formula: ((ai_prob - market_price) / market_price) * 100

    Both inputs must be in 0.0-1.0 range (probability units).
    Returns 0.0 if inputs are invalid or edge is negative.
    Caps edge at MAX_EDGE_PCT (500%) with a warning.
    """
    edge, _ = calculate_edge_pct_with_flag(ai_prob, market_price, market_id)
    return edge


def _get_vol_model_days_to_expiry(market: dict, parsed_ticker: dict) -> float:
    days_to_end = market.get("_days_to_end")
    if isinstance(days_to_end, (int, float)) and days_to_end > 0 and days_to_end != float("inf"):
        return float(days_to_end)

    expiry_date = parsed_ticker.get("expiry_date")
    if expiry_date is None:
        return 0.0
    return float((expiry_date - datetime.now(timezone.utc).date()).days)


def _get_min_vol_prob() -> float:
    try:
        return float(os.getenv("MIN_VOL_PROB", str(MIN_VOL_PROB)) or "0.0")
    except (TypeError, ValueError):
        return MIN_VOL_PROB


def _passes_vol_gate(vol_prob: float | None, min_vol_prob: float | None = None) -> bool:
    """Return True when the vol gate should allow a market through."""
    threshold = _get_min_vol_prob() if min_vol_prob is None else float(min_vol_prob)
    if threshold <= 0.0 or vol_prob is None:
        return True
    return float(vol_prob) >= threshold


def _apply_vol_model_or_shrinkage(market: dict, ai_prob: float) -> float:
    ticker = market.get("ticker", market.get("id", "?"))
    market["_ai_raw_probability"] = ai_prob
    market["_vol_model"] = None
    market["_vol_model_prob"] = None
    market["_vol_gate_min"] = _get_min_vol_prob()
    market["_vol_gate_rejected"] = False
    market["_calibration_used"] = False
    market["_calibrated_probability"] = None
    market["_calibration_bucket"] = None
    market["_calibration_trades"] = 0

    parsed = parse_kalshi_index_ticker(ticker) if parse_kalshi_index_ticker is not None else None
    if parsed is not None and compute_vol_probability is not None:
        days_to_expiry = _get_vol_model_days_to_expiry(market, parsed)
        vol_result = compute_vol_probability(
            parsed["prefix"],
            parsed["strike"],
            days_to_expiry,
            parsed.get("direction", "above"),
        )
        if vol_result is not None:
            blended_prob = blend_probability(ai_prob, vol_result["vol_prob"])
            market["_vol_model"] = vol_result
            market["_vol_model_prob"] = vol_result["vol_prob"]
            market["_blended_probability"] = blended_prob
            min_vol_prob = market["_vol_gate_min"]
            logger.info(
                "[VOL_MODEL] ticker=%s ai_prob=%.3f vol_prob=%.3f blended=%.3f",
                ticker,
                ai_prob,
                vol_result["vol_prob"],
                blended_prob,
            )
            if not _passes_vol_gate(vol_result["vol_prob"], min_vol_prob):
                market["_vol_gate_rejected"] = True
                logger.info(
                    "[VOL_GATE] Rejecting %s: vol_prob %.3f < min %.3f",
                    ticker,
                    vol_result["vol_prob"],
                    min_vol_prob,
                )
                return 0.5
            return blended_prob

    logger.info("[VOL_MODEL] Fallback to calibration for %s", ticker)
    adjusted_prob = apply_probability_shrinkage(ai_prob)
    bucket = None
    try:
        if build_calibration_table is not None and calibrate_probability is not None:
            table = build_calibration_table()
            bucket = (
                nearest_bucket_name(ai_prob, table)
                if nearest_bucket_name is not None
                else None
            )
            if bucket is None and calibration_bucket_name is not None:
                bucket = calibration_bucket_name(ai_prob)
            adjusted_prob = calibrate_probability(ai_prob, table)
            total = int(table.get("total_calibration_trades") or 0)
            enough_data = bool(table.get("enough_data"))
            market["_calibration_trades"] = total
            market["_calibration_bucket"] = bucket
            market["_calibration_used"] = enough_data and bucket is not None
            if market["_calibration_used"]:
                market["_calibrated_probability"] = adjusted_prob
            logger.info(
                "[CALIBRATION] ticker=%s raw=%.3f calibrated=%.3f bucket=%s trades=%d used=%s",
                ticker,
                ai_prob,
                adjusted_prob,
                bucket or "fallback",
                total,
                "yes" if market["_calibration_used"] else "fallback",
            )
            if not enough_data:
                logger.warning(
                    "[CALIBRATION] WARN insufficient settled AI trades: %d < 50; using flat shrinkage",
                    total,
                )
        else:
            logger.info("[CALIBRATION] provider unavailable for %s; using flat shrinkage", ticker)
    except Exception as exc:
        logger.warning("[CALIBRATION] failed for %s: %s; using flat shrinkage", ticker, exc)
    market["_blended_probability"] = adjusted_prob
    return adjusted_prob


def _extract_market_category(ticker: str, series_category: str = None) -> str:
    """Extract market category from series metadata or ticker prefix."""
    # Prefer series_category if available
    if series_category:
        cat = series_category.lower().strip()
        if cat in ("sports", "esports", "entertainment", "social"):
            return cat
        if cat in ("index", "crypto", "economics", "commodities", "financials", "politics"):
            return cat
        if cat in ("other", "unknown", ""):
            return "other"
    # Fallback to ticker prefix
    prefix = ticker.split("-")[0] if "-" in ticker else ticker[:12]
    for key, cat in _TICKER_CATEGORY_MAP.items():
        if prefix.startswith(key):
            return cat
    if "SPORTS" in ticker or "NFL" in ticker or "NBA" in ticker:
        return "sports"
    if "INX" in ticker or "NASDAQ" in ticker:
        return "index"
    if "BTC" in ticker or "ETH" in ticker:
        return "crypto"
    return "other"


def _is_category_allowed(category: str, mode: str) -> bool:
    """Check if a category is allowed for trading in the given mode."""
    cat = category.lower().strip()
    # In shadow mode, allow everything but tag it
    if mode == "shadow":
        return True
    # Live modes: only allowlisted categories
    return cat in ALLOWED_CATEGORIES


def _get_todays_realized_pnl(db_path: str = "/opt/slimy/pm_updown_bot_bundle/paper_trading/pnl.db") -> float:
    """Query pnl.db for today's realized PnL from executed trades."""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Sum realized_pnl_usd for trades with status='executed' or 'settled' today
        cursor.execute(
            """SELECT COALESCE(SUM(realized_pnl_usd), 0.0) FROM trades 
               WHERE date(timestamp) = ? AND status IN ('executed', 'settled')""",
            (today,)
        )
        result = cursor.fetchone()[0]
        conn.close()
        return float(result) if result else 0.0
    except Exception as e:
        logger.warning("[DAILY_LOSS] Could not query today's PnL: %s", e)
        # Fail closed: if we can't determine PnL, assume we've lost the max
        # unless explicitly allowed
        if os.getenv("ALLOW_UNKNOWN_DAILY_PNL", "false").lower() in ("true", "1", "yes"):
            return 0.0
        return -float("inf")

def check_micro_live_gates(market, size, price, risk_caps, venue, computed_edge_pct=None):
    """
    Micro-live risk gates - must pass ALL to execute real trades

    Args:
        computed_edge_pct: Pre-computed edge after fees (optional). If provided,
                           this takes precedence over market.get("edge_pct").
    Returns: (passed: bool, violations: list)
    """
    from config import MIN_PRICE_CENTS, SKIP_FALLBACK_PRIORS
    violations = []

    # Gate 0: Minimum price floor (Becker trap protection)
    price_cents = int(round(price * 100))
    if price_cents < MIN_PRICE_CENTS:
        violations.append(
            f"[GATE] Rejected {market.get('ticker', market.get('id', '?'))}: "
            f"price {price_cents}c < {MIN_PRICE_CENTS}c minimum floor (Becker trap protection)"
        )

    # Gate F: Fallback prior rejection
    if SKIP_FALLBACK_PRIORS and market.get("_ai_prior_is_fallback"):
        violations.append(
            f"[GATE] Rejected {market.get('ticker', market.get('id', '?'))}: "
            f"fallback prior (cascade failed, no real AI view)"
        )

    # Gate 1: Position size limit
    if size > risk_caps.get("max_pos_usd", 10):
        violations.append(f"Size ${size:.2f} > max ${risk_caps['max_pos_usd']}")

    # Gate 2: Minimum liquidity
    liquidity = market.get("volume_24h", 0) or market.get("liquidity_usd", 0)
    min_liq = risk_caps.get("liquidity_min_usd", 1000)
    if liquidity < min_liq:
        violations.append(f"Liquidity ${liquidity:.0f} < min ${min_liq}")

    # Gate 3: Edge after fees — use computed value if available
    if computed_edge_pct is not None:
        edge = computed_edge_pct
    else:
        edge = market.get("edge_pct", 0) or market.get("expected_edge_pct", 0)
    min_edge = risk_caps.get("edge_after_fees_pct", 0.5)
    if edge < min_edge:
        violations.append(f"Edge {edge:.1f}% < min {min_edge}%")
    
    # Gate 4: Market end time (must be > 24h away for Kalshi)
    end_time = market.get("close_time") or market.get("expiration_date")
    if end_time:
        try:
            if isinstance(end_time, str):
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            else:
                end_dt = end_time
            hours_left = (end_dt - datetime.now(end_dt.tzinfo)).total_seconds() / 3600
            min_hours = risk_caps.get("market_end_hrs", 24)
            if hours_left < min_hours:
                violations.append(f"Market ends in {hours_left:.1f}h < min {min_hours}h")
        except:
            pass  # If we can't parse time, allow it
    
    # Gate 5: Price sanity (not too extreme)
    if price < 0.02 or price > 0.98:
        violations.append(f"Price {price:.2f} too extreme")
    
    passed = len(violations) == 0
    return passed, violations


def gate_failure_kinds_from_violations(violations):
    """Classify existing gate violation text into redacted diagnostic labels."""
    kinds = []
    for violation in violations:
        text = str(violation).lower()
        if "fallback prior" in text:
            kind = "fallback_prior"
        elif text.startswith("size ") or " > max $" in text:
            kind = "size_limit"
        elif text.startswith("liquidity "):
            kind = "liquidity_min"
        elif text.startswith("edge ") or " edge " in text:
            kind = "edge_below_threshold"
        elif text.startswith("market ends in"):
            kind = "market_end_time"
        elif text.startswith("price ") and "too extreme" in text:
            kind = "price_sanity"
        elif "minimum floor" in text:
            kind = "min_price_floor"
        else:
            kind = "unknown_gate_failure"
        if kind not in kinds:
            kinds.append(kind)
    return kinds or ["unknown_gate_failure"]

# Load environment
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('/opt/slimy/pm_updown_bot_bundle/logs/runner-optimized.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def estimate_true_price(
    market_question: str,
    market_id: str,
    tier: str = "premium",
    timeout: float = None,
) -> float:
    """
    Estimate YES probability via AI cascade; fallback to 0.5.

    Args:
        market_question: Market question text.
        market_id: Market ticker/id.
        tier: AI tier ("premium" or "bulk").
        timeout: Per-call timeout in seconds. If provided, passed to
                 get_ai_prior so a slow provider call doesn't blow past budget.
    """
    if get_ai_prior is None:
        logger.warning(
            "[kelly] AI prior: source=fallback_import_missing prob=0.500 market=%s",
            market_id,
        )
        return 0.5

    try:
        prob = float(get_ai_prior(market_question, tier=tier, market_ticker=market_id, timeout=timeout))
        if 0.0 <= prob <= 1.0:
            return prob
    except Exception as exc:
        logger.warning("[kelly] AI prior failed: market=%s err=%s", market_id, exc)

    return 0.5

def calculate_probability_weighted_fee(price: float, quantity: float) -> float:
    """
    Calculate Kalshi's probability-weighted fee
    
    Kalshi fee formula: 0.07 × price × (1 - price)
    
    Args:
        price: Contract price (0.01 to 0.99)
        quantity: Number of contracts
    
    Returns:
        Total fee in USD
    """
    
    # The probability-weighted fee peaks at 50¢ (17.5% of contract)
    # At 37% odds: Fee = 0.07 × 0.37 × (1 - 0.37) = 0.07 × 0.37 × 0.63 = 1.64175¢
    # At 50¢ odds: Fee = 0.07 × 0.50 × (1 - 0.50) = 0.07 × 0.50 × 0.50 = 1.75¢
    
    # Calculate fee
    fee = 0.07 * price * (1 - price)
    
    # Multiply by quantity
    total_fee = fee * quantity
    
    return round(total_fee, 4)  # Round to nearest cent

def get_maker_fee(price: float) -> float:
    """
    Calculate if maker order costs $0 (for this market)
    
    Maker orders are free ONLY when market is priced at EXACTLY 50¢
    """
    
    # Check if price is exactly 50 cents
    if price == 0.50:
        return 0.0
    
    return 0.07 * price  # Taker fee (maker orders charge taker fee on fill)

def is_maker_profitable(price: float, true_price: float, win_prob: float, min_edge_pct: float = 0.5) -> bool:
    """
    Determine if maker order would be profitable after fees
    
    Args:
        price: Current market price
        true_price: Your estimated true probability
        win_prob: Probability you're correct
        min_edge_pct: Minimum edge to justify trade (default 0.5%)
    
    Returns:
        True if expected value > cost after fees
    """
    
    # Calculate expected value
    if win_prob > 0.5:
        # If you win, expected value = price (1.0)
        expected_value = price
    else:
        # If you lose, expected value = 0
        expected_value = 0
    
    # Calculate fee (use maker fee if available, otherwise taker)
    fee = get_maker_fee(price) if win_prob > 0.5 else 0.07 * price
    
    # Calculate cost after fees
    cost = price + fee
    
    # Calculate expected profit
    expected_profit = expected_value - cost
    
    # Calculate edge percentage
    if price > 0:
        edge_pct = ((expected_profit / price) * 100) if price > 0 else 0
    else:
        edge_pct = 0
    
    return edge_pct >= min_edge_pct

def find_best_maker_market(markets: list, min_edge_pct: float = 0.5) -> dict:
    """
    Find best market for maker orders
    
    Args:
        markets: List of Kalshi markets
        min_edge_pct: Minimum edge to justify maker order (default 0.5%)
    
    Returns:
        Dictionary with market ID, price, true_price, maker_fee
    """
    best_market = None
    best_edge_pct = -999.0  # Initialize with very low value
    
    for market in markets:
        # Get market data
        yes_price = market.get("odds", {}).get("yes")
        if yes_price is None or yes_price <= 0:
            continue
        market_question = market.get("title", market.get("question", ""))
        market_id = market.get("ticker", market.get("id", "?"))
        true_price = market.get("_ai_true_price")
        if true_price is None:
            true_price = estimate_true_price(market_question, market_id)
        maker_fee = get_maker_fee(yes_price)
        
        # Calculate edge using validated helper
        edge_pct = calculate_edge_pct(true_price, yes_price, market_id)
        
        # Check if maker is profitable
        if edge_pct > 0 and is_maker_profitable(yes_price, true_price, 0.6):  # 60% win prob
            # Check if this market has better edge than current best
            if edge_pct > best_edge_pct:
                best_edge_pct = edge_pct
                best_market = market
        
        logger.debug(
            f"Market {market.get('id')}: price={yes_price:.4f}, "
            f"true={true_price:.3f}, edge={edge_pct:.2f}%, maker_fee={maker_fee:.2f}¢"
        )
    
    if best_market is None:
        logger.warning("No profitable maker markets found")
    
    return best_market

def calculate_optimal_order_size(bankroll: float, num_markets: int, risk_cap_usd: float = 10.0) -> float:
    """
    Calculate optimal order size across all profitable markets
    
    Args:
        bankroll: Available capital
        num_markets: Number of markets to trade
        risk_cap_usd: Maximum position size
    """
    
    # Calculate total allocation
    max_pos_total = risk_cap_usd * num_markets  # $10 * 5 markets = $50 max exposure
    
    if bankroll > max_pos_total:
        # Can afford to size each market equally
        optimal_size = risk_cap_usd
    else:
        # Bankroll is limiting factor - split evenly
        optimal_size = bankroll / num_markets
    
    # Ensure minimum order size
    min_size = 0.01  # $0.01 minimum
    
    optimal_size = max(optimal_size, min_size)
    
    logger.info(f"Optimal order size: ${optimal_size:.2f} per market (bankroll: ${bankroll:.2f}, risk_cap: ${risk_cap_usd:.2f}, num_markets: {num_markets})")
    
    return optimal_size

def optimize_trade_frequency(current_frequency: int, optimal_edge_pct: float, min_hours: float = 0.0) -> int:
    """
    Reduce trade frequency to focus on higher-edge markets
    
    Args:
        current_frequency: Current trades per day
        optimal_edge_pct: Minimum edge to justify trading (default 0.5%)
        min_hours: Minimum hours between market checks (default 0.5 = 30 minutes)
    
    Returns:
        Recommended new frequency (higher = better quality, lower = reduce frequency)
    """
    
    # Lower frequency only if edge is significantly higher than current
    if optimal_edge_pct > current_frequency * 2:  # Edge > 2x current frequency
        # Reduce to half frequency (market checks less often)
        new_frequency = max(current_frequency // 2, 1)
        logger.info(f"Reducing frequency: {current_frequency} -> {new_frequency} (edge improved from {current_frequency * 2:.1f}% to {optimal_edge_pct:.1f}%)")
    else:
        # Keep current frequency (edge is good enough)
        new_frequency = current_frequency
        logger.info(f"Frequency unchanged: {new_frequency} (current edge: {current_frequency * 2:.1f}% >= optimal {optimal_edge_pct:.1f}%)")
    
    # Ensure minimum time between market checks
    min_checks_per_hour = int(60 / min_hours)  # 2 checks per hour = 30 minute intervals
    new_frequency = min(new_frequency, min_checks_per_hour)
    
    return new_frequency

def filter_low_liquidity_markets(markets: list, min_liquidity_usd: float = 0.0, max_trades: int = 20) -> list:
    """
    Filter out low-liquidity markets (won't get good fills)
    
    Args:
        markets: List of markets
        min_liquidity_usd: Minimum liquidity required
        max_trades: Maximum number of trades to place in batch
    
    Returns:
        Filtered markets
    """
    
    filtered = []
    for market in markets:
        liquidity_usd = market.get("liquidity_usd", 0.0)
        
        # Only skip very low liquidity markets
        if liquidity_usd < min_liquidity_usd:
            logger.debug(f"Skipping {market.get('id')}: liquidity ${liquidity_usd:.2f} < ${min_liquidity_usd:.2f}")
            continue
        
        # Only trade markets with reasonable liquidity
        filtered.append(market)
    
    logger.info(f"Filtered {len(filtered)} markets from {len(markets)} (liquidity >= ${min_liquidity_usd:.2f})")
    
    return filtered

def get_edge_after_fees(market: dict, true_price: float = None) -> float:
    """
    Calculate edge percentage after fees
    
    Args:
        market: Market data
    
    Returns:
        Edge percentage (after fees)
    """
    
    yes_price = market.get("odds", {}).get("yes", 0.0)
    if yes_price <= 0:
        return 0.0
    if true_price is None:
        true_price = market.get("_ai_true_price")
    if true_price is None:
        market_question = market.get("title", market.get("question", ""))
        market_id = market.get("ticker", market.get("id", "?"))
        true_price = estimate_true_price(market_question, market_id)
    
    # Use validated edge helper
    edge_before_fees_pct = calculate_edge_pct(true_price, yes_price, market.get("id", "?"))
    
    # Calculate edge after fees (using maker if available)
    if yes_price == 0.50:
        # Maker order costs $0
        edge_after_fees_pct = edge_before_fees_pct
    else:
        # Maker order charges taker fee on fill
        maker_fee = 0.07 * yes_price
        if true_price <= 0:
            edge_after_fees_pct = 0
        else:
            # Fee-adjusted edge: account for maker fee
            fee_adjusted_price = yes_price + maker_fee
            edge_after_fees_pct = calculate_edge_pct(true_price, fee_adjusted_price, market.get("id", "?"))
    
    logger.debug(f"Market {market.get('id')}: price={yes_price:.4f}, edge_before={edge_before_fees_pct:.2f}%, edge_after={edge_after_fees_pct:.2f}%")
    
    return edge_after_fees_pct

def optimize_kalshi_strategy(
    mode: str,
    bankroll: float = 100.0,
    max_pos_usd: float = 10.0,
    dry_run: bool = True,
    min_edge_override: float = None,
    scratchpad=None,
    stage_budget=None,
    cursor=None,
):
    """
    Main function for Phase 1 Kalshi optimization

    Args:
        mode: 'shadow' or 'real-live'
        bankroll: Available capital in USD
        max_pos_usd: Maximum position size
        dry_run: If True, only simulate without executing
        scratchpad: Scratchpad instance for event logging (optional)
        stage_budget: StageBudget instance for the ai_cascade stage.
                      If provided, budget.exhausted() is checked before each
                      per-market prior call, and budget.remaining() is passed
                      as the per-call timeout. On exhaustion, the cascade loop
                      breaks with whatever priors were collected.

    Returns:
        Number of orders placed
    """
    
    logger.info("=" * 60)
    logger.info("KALSHI OPTIMIZATION - PHASE 1 (Quick Wins)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    if min_edge_override is not None:
        logger.info(f"Regime min_edge override: {min_edge_override:.2f}%")
    logger.info("=" * 60)

    # ── Micro-live normalization ─────────────────────────────────────────────
    # micro-live = real-live with hard caps (no code duplication)
    is_live = mode in ("real-live", "micro-live")
    MICRO_LIVE_LOG = "[MICRO-LIVE]"
    if mode == "micro-live":
        bankroll = min(bankroll, 25.0)
        max_pos_usd = min(max_pos_usd, 5.0)
        max_daily_loss = 10.0
        logger.info(
            "%s Hard caps applied: bankroll=$%.2f, max_pos=$%.2f, max_daily_loss=$%.2f",
            MICRO_LIVE_LOG, bankroll, max_pos_usd, max_daily_loss,
        )

    # Get risk caps
    risk_caps = {
        "max_pos_usd": max_pos_usd,
        "max_daily_loss_usd": 50.0,
        "max_open_pos": 5,
        "max_daily_positions": 20,
        "liquidity_min_usd": 500.0,
        "edge_after_fees_pct": 3.0,
        "market_end_hrs": 0
    }

    capture_runtime = None
    if create_candidate_capture is not None:
        try:
            capture_runtime = create_candidate_capture(
                mode=mode,
                run_id=str(getattr(stage_budget, "_cron_run_id", "standalone-shadow-run")),
            )
        except Exception:
            logger.warning("[SHADOW_CAPTURE] adapter initialization failed safely")
    
    # Fetch Kalshi markets
    logger.info("Fetching Kalshi markets...")
    markets = fetch_kalshi_markets()
    
    if not markets:
        logger.warning("No markets fetched")
        _flush_shadow_candidates(capture_runtime)
        return 0
    
    logger.info(f"Fetched {len(markets)} markets")
    
    # Filter for liquidity
    markets = filter_low_liquidity_markets(markets, min_liquidity_usd=0.0, max_trades=20)

    # ── Rotating market cursor (Reliability Phase 1, Module 1.5) ──────
    cursor_hash_changed = False
    if cursor is not None and _rotate_markets is not None:
        result = _rotate_markets(markets, cursor)
        rotated_markets, updated_cursor, cursor_hash_changed = result
        cursor.index = updated_cursor.index
        cursor.list_hash = updated_cursor.list_hash
        cursor.total_rotations = updated_cursor.total_rotations
        if cursor_hash_changed:
            logger.warning(
                "[cursor] Market list hash changed, reset cursor to 0 (run_id=%s)",
                getattr(stage_budget, "_cron_run_id", "n/a"),
            )
            if scratchpad is not None:
                scratchpad.log(
                    "cursor_reset",
                    cron_run_id=getattr(stage_budget, "_cron_run_id", "n/a"),
                    old_hash=result[1].list_hash if cursor_hash_changed else cursor.list_hash,
                    new_hash=updated_cursor.list_hash,
                    reason="list_changed",
                )
        markets = rotated_markets
        logger.info("[cursor] Rotation applied: index=%d list_size=%d", updated_cursor.index, len(markets))
        if scratchpad is not None:
            scratchpad.log(
                "cursor_state",
                cron_run_id=getattr(stage_budget, "_cron_run_id", "n/a"),
                index=updated_cursor.index,
                list_hash=updated_cursor.list_hash,
                total_rotations=updated_cursor.total_rotations,
                list_size=len(markets),
            )
    # ────────────────────────────────────────────────────────────────────

    # Pre-dedup: exclude markets we already own or have open orders on
    if is_live:
        try:
            from utils.kalshi_orders import KalshiOrderClient
            order_client = KalshiOrderClient()
            existing_orders = order_client.get_orders(status="resting") or []
            existing_positions = order_client.get_positions() or []
            existing_tickers = set()
            for o in existing_orders:
                t = o.get("ticker") or o.get("market_ticker")
                if t:
                    existing_tickers.add(t)
            for p in existing_positions:
                t = p.get("ticker") or p.get("market_ticker")
                if t:
                    existing_tickers.add(t)
            before = len(markets)
            # Check both market.id and market.ticker against existing order tickers
            markets = [
                m for m in markets
                if m.get("id") not in existing_tickers
                and m.get("ticker") not in existing_tickers
            ]
            logger.info(
                "[PREMIUM] Pre-dedup: %d -> %d markets (excluded %d existing)",
                before,
                len(markets),
                len(existing_tickers),
            )
        except Exception as e:
            logger.warning("[PREMIUM] Pre-dedup failed: %s", e)

    # ── Expiry filter: reject markets too far in the future ─────────────
    now_ts = datetime.now(timezone.utc).timestamp()
    before_expiry = len(markets)
    markets_filtered = []
    for m in markets:
        end_time = m.get("close_time") or m.get("expiration_date")
        days_left = None
        if end_time:
            try:
                if isinstance(end_time, str):
                    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                else:
                    end_dt = end_time
                days_left = (end_dt.timestamp() - now_ts) / 86400
            except Exception:
                days_left = None
        m["_days_to_end"] = days_left if days_left is not None else float("inf")
        
        # In live modes, reject missing/unparseable expiry
        is_live = mode in ("real-live", "micro-live")
        if days_left is None:
            if is_live:
                logger.info(
                    "[EXPIRY] Skipping %s: missing/unparseable expiry",
                    m.get("ticker", m.get("id", "?")),
                )
                continue
            else:
                # Shadow mode: tag but don't skip
                m["_expiry_unsafe"] = True
                logger.debug(
                    "[EXPIRY] Tagging %s: missing/unparseable expiry (shadow)",
                    m.get("ticker", m.get("id", "?")),
                )
        elif days_left > MAX_DAYS_TO_EXPIRY:
            logger.info(
                "[EXPIRY] Skipping %s: %.0f days to expiry > max %.0f",
                m.get("ticker", m.get("id", "?")),
                days_left,
                MAX_DAYS_TO_EXPIRY,
            )
            continue
        markets_filtered.append(m)
    markets = markets_filtered
    logger.info(
        "[EXPIRY] Filtered %d -> %d markets (max_days=%.0f)",
        before_expiry,
        len(markets),
        MAX_DAYS_TO_EXPIRY,
    )

    # ── Category filter: reject disallowed categories in live modes ─────
    before_cat = len(markets)
    markets_filtered = []
    for m in markets:
        ticker = m.get("ticker", m.get("id", "?"))
        series_cat = m.get("series_category") or m.get("category")
        category = _extract_market_category(ticker, series_cat)
        m["_category"] = category
        if not _is_category_allowed(category, mode):
            logger.info(
                "[CATEGORY] Skipping %s: category=%s not in allowlist",
                ticker,
                category,
            )
            continue
        markets_filtered.append(m)
    markets = markets_filtered
    logger.info(
        "[CATEGORY] Filtered %d -> %d markets (allowed=%s)",
        before_cat,
        len(markets),
        ",".join(sorted(ALLOWED_CATEGORIES)),
    )

    # Split premium into two volume-sorted buckets: short-term (<=7d) and long-term (>7d)
    # Each bucket gets up to 10 markets; together they form the 20-market AI premium tier
    SHORT_DAYS = 7
    SHORT_MIN_VOL = 0
    SHORT_MAX = 10
    LONG_MAX = 10

    short_bucket = []
    long_bucket = []
    for m in markets:
        days_left = m.get("_days_to_end", float("inf"))
        vol = m.get("volume_24h", 0) or m.get("liquidity_usd", 0)
        if days_left <= SHORT_DAYS and vol > SHORT_MIN_VOL:
            short_bucket.append(m)
        else:
            long_bucket.append(m)

    # Cap long-term bucket at MAX_LONG_TERM_DAYS
    long_bucket_filtered = []
    for m in long_bucket:
        days_left = m.get("_days_to_end", float("inf"))
        if days_left != float("inf") and days_left > MAX_LONG_TERM_DAYS:
            logger.info(
                "[EXPIRY] Skipping long-term %s: %.0f days > max_long_term %.0f",
                m.get("ticker", m.get("id", "?")),
                days_left,
                MAX_LONG_TERM_DAYS,
            )
            continue
        long_bucket_filtered.append(m)
    long_bucket = long_bucket_filtered

    short_bucket.sort(key=lambda m: m.get("volume_24h", 0) or m.get("liquidity_usd", 0), reverse=True)
    long_bucket.sort(key=lambda m: m.get("volume_24h", 0) or m.get("liquidity_usd", 0), reverse=True)

    short_premium = short_bucket[:SHORT_MAX]
    long_premium = long_bucket[:LONG_MAX]
    premium_markets = short_premium + long_premium

    logger.info(
        "[PREMIUM] Short-term: %d, Long-term: %d, Total: %d (max %d each)",
        len(short_premium),
        len(long_premium),
        len(premium_markets),
        SHORT_MAX,
    )

    # Tag premium markets; bulk tier absorbs remaining markets up to ai_bulk_max
    for m in markets:
        m["_ai_tier"] = "skip"
        m["_ai_true_price"] = 0.5

    for m in premium_markets:
        m["_ai_tier"] = "premium"

    ai_bulk_max = 0
    bulk_count = 0
    for m in markets:
        if m["_ai_tier"] == "premium":
            continue
        if bulk_count < ai_bulk_max:
            m["_ai_tier"] = "bulk"
            bulk_count += 1
        if bulk_count >= ai_bulk_max:
            break

    # AI cascade loop with per-market budget check (Reliability Phase 1, Module 1)
    cascade_markets = [m for m in markets if m.get("_ai_tier") != "skip"]
    cascade_skipped = 0
    cascade_attempted = 0
    for m in cascade_markets:
        if stage_budget is not None and stage_budget.exhausted():
            logger.warning(
                "[budget] ai_cascade exhausted: %d/%d markets got priors, %d skipped (run_id=%s)",
                len([x for x in cascade_markets if x.get("_ai_true_price", None) is not None]),
                len(cascade_markets),
                cascade_skipped,
                getattr(stage_budget, "_cron_run_id", "n/a"),
            )
            if stage_budget is not None:
                stage_budget._items_skipped += (len(cascade_markets) - cascade_skipped)
            if scratchpad is not None:
                scratchpad.log(
                    "cascade_budget_exhausted",
                    stage="ai_cascade",
                    markets_with_priors=len([x for x in cascade_markets if x.get("_ai_true_price", 0.5) != 0.5]),
                    markets_total=len(cascade_markets),
                    cron_run_id=getattr(stage_budget, "_cron_run_id", "n/a"),
                )
            break

        per_call_timeout = stage_budget.remaining() if stage_budget else None
        if dry_run and per_call_timeout is not None:
            per_call_timeout = min(per_call_timeout, SHADOW_PER_CALL_TIMEOUT_CAP)
        ai_prob = estimate_true_price(
            m.get("title", m.get("question", "")),
            m.get("ticker", m.get("id", "?")),
            tier=m["_ai_tier"],
            timeout=per_call_timeout,
        )
        m["_ai_true_price"] = _apply_vol_model_or_shrinkage(m, ai_prob)
        if _was_last_prior_fallback is not None:
            m["_ai_prior_is_fallback"] = _was_last_prior_fallback()
        else:
            m["_ai_prior_is_fallback"] = (ai_prob == 0.5)
        cascade_attempted += 1
        if stage_budget is not None:
            stage_budget.mark_processed()
    else:
        # Loop completed normally (no break)
        if stage_budget is not None:
            stage_budget._items_skipped = 0
    
    # Calculate optimal order size using only markets with real AI priors
    ai_markets = [m for m in markets if m.get("_ai_true_price", 0.5) != 0.5]
    num_ai_markets = max(len(ai_markets), 1)
    optimal_size = calculate_optimal_order_size(bankroll, num_ai_markets, risk_caps["max_pos_usd"])
    logger.info(f"Optimal order size: ${optimal_size:.2f} per market (Kelly on {num_ai_markets} AI-priced markets)")
    
    # Find best maker market (BONUS optimization — not a gate for order execution)
    effective_min_edge = min_edge_override if min_edge_override is not None else risk_caps["edge_after_fees_pct"]
    logger.info("Finding best maker markets...")
    best_maker_market = find_best_maker_market(markets, effective_min_edge)

    if best_maker_market:
        logger.info(f"Best maker market: {best_maker_market.get('id')} at {best_maker_market.get('odds', {}).get('yes', 0.0):.4f} (maker fee: {get_maker_fee(best_maker_market.get('odds', {}).get('yes', 0.0)):.2f}¢)")
    else:
        logger.info("No profitable maker markets found — will use limit orders on AI-priced markets")
    
    # Track metrics
    total_trades = 0
    total_filled = 0
    total_volume = 0.0
    orders = []
    proof_data = {
        "mode": mode,
        "bankroll": bankroll,
        "max_pos_usd": max_pos_usd,
        "orders_placed": [],
        "orders_failed": [],
    }
    nearest_misses = []
    order_intent_count = 0
    order_submission_attempted_count = 0
    order_submission_succeeded_count = 0
    order_submission_failed_count = 0
    submission_skipped_reason_counts = {}
    post_intent_blocker_counts = {}
    price_gate_blocked_count = 0
    edge_or_profitability_blocked_count = 0
    no_profitable_maker_count = 0 if best_maker_market else 1
    
    for market in markets:
        market_id = market.get("id")
        yes_price = market.get("odds", {}).get("yes", 0.0)
        true_price = market.get("_ai_true_price", 0.5)
        ai_raw_probability = market.get("_ai_raw_probability", true_price)
        vol_model_probability = market.get("_vol_model_prob")
        vol_gate_min = market.get("_vol_gate_min", _get_min_vol_prob())
        calibrated_probability = market.get("_calibrated_probability")
        calibration_bucket = market.get("_calibration_bucket")
        calibration_trades = market.get("_calibration_trades")

        # Skip markets with no AI signal (tier="skip" = no API call was made)
        if market.get("_ai_tier") == "skip":
            logger.debug(f"Market {market_id}: tier=skip, skipping — no AI signal")
            continue

        # AI prior self-validation gate
        if validate_prior is not None:
            hours_to_end = market.get("hours_to_end", 48)
            vol_conf = volume_confidence(
                int(market.get("liquidity_usd", 0) / max(yes_price, 0.01)),
                median_volume=500,
            ) if volume_confidence else None
            exp_conf = expiry_confidence(hours_to_end) if expiry_confidence else None

            price_history = market.get("price_history", [])
            if price_history:
                mom = compute_momentum(price_history, window=5) if compute_momentum else None
                zsc = compute_zscore(price_history, window=10) if compute_zscore else None
            else:
                mom = None
                zsc = None

            val_result = validate_prior(
                prior=true_price,
                momentum=mom,
                zscore=zsc,
                contract_price=yes_price,
            )
            market["_validation"] = val_result
            market["_adjusted_prior"] = val_result["adjusted_prior"]

            # Always log validation result
            if val_result["passed"]:
                logger.info(
                    "[kelly] prior validation PASSED market=%s prior=%.3f conf=%.2f flags=%s",
                    market_id,
                    true_price,
                    val_result["confidence"],
                    val_result["flags"],
                )
            else:
                logger.info(
                    "[kelly] prior validation FAILED market=%s prior=%.3f reason=%s flags=%s",
                    market_id,
                    true_price,
                    val_result["reason"],
                    val_result["flags"],
                )

            # Write to scratchpad
            if scratchpad is not None:
                scratchpad.log_prior_validation(
                    market=market_id,
                    prior=true_price,
                    val_result=val_result,
                    passed=val_result["passed"],
                )

            if not val_result["passed"]:
                # Capture must remain complete even when the optional nearest-miss
                # helper import is unavailable.  This fallback is observational
                # only; the guarded helpers below still own diagnostic precision.
                prior_raw_edge = max(
                    0.0,
                    min(MAX_EDGE_PCT, ((true_price - yes_price) / yes_price) * 100.0),
                )
                prior_fee_edge = None
                if make_nearest_miss is not None:
                    prior_raw_edge = calculate_edge_pct(true_price, yes_price, market_id)
                    prior_fee_edge = get_edge_after_fees(market, true_price=true_price)
                    nearest_misses.append(make_nearest_miss(
                        market=market,
                        side="yes",
                        price=yes_price,
                        ai_prior=true_price,
                        raw_edge=prior_raw_edge,
                        fee_adjusted_edge=prior_fee_edge,
                        required_threshold=risk_caps["edge_after_fees_pct"],
                        rejection_reason="prior_validation_failed",
                    ))
                _record_shadow_candidate(
                    capture_runtime,
                    market=market,
                    observed_price=yes_price,
                    ai_prior=true_price,
                    fallback_prior_used=bool(market.get("_ai_prior_is_fallback")),
                    raw_edge=prior_raw_edge,
                    fee_adjusted_edge=prior_fee_edge,
                    required_threshold=risk_caps["edge_after_fees_pct"],
                    rejection_reason="prior_validation_failed",
                    gate_failure_kinds=["prior_validation"],
                    order_intent_created=False,
                    expected_value=prior_fee_edge,
                )
                continue

            # Use adjusted prior for sizing if validation passed
            effective_prior = val_result["adjusted_prior"]
        else:
            effective_prior = true_price

        # Use validated/adjusted prior for all downstream Kelly sizing
        true_price = effective_prior

        # Gate 2: Long-dated prior cap (Becker trap protection)
        # Long-dated markets get inflated LLM priors that create illusory edge.
        # Cap the prior to at most (market_price + 0.15) for markets > 30d expiry.
        from config import LONG_DATED_EXPIRY_DAYS, LONG_DATED_PRIOR_CAP_ABOVE_MARKET
        _days_to_end = market.get("_days_to_end")
        if _days_to_end is not None and _days_to_end != float("inf"):
            if _days_to_end > LONG_DATED_EXPIRY_DAYS:
                market_price_prob = yes_price
                cap = market_price_prob + LONG_DATED_PRIOR_CAP_ABOVE_MARKET
                if true_price > cap:
                    raw_prior = true_price
                    true_price = cap
                    logger.info(
                        "[GATE] Long-dated prior capped: %s %.0fd expiry, prior %.3f -> %.3f (market+%.2f cap)",
                        market_id, _days_to_end, raw_prior, cap, LONG_DATED_PRIOR_CAP_ABOVE_MARKET,
                    )
                    market["_prior_capped"] = True
                    market["_prior_raw"] = raw_prior
                    market["_prior_capped_to"] = cap

        # Calculate edge after fees
        edge_after_fees_pct = get_edge_after_fees(market, true_price=true_price)

        # ── Edge sanity: reject capped / insane raw edge ─────────────────
        # A capped edge means the model/price comparison is too extreme or noisy.
        # This is NOT a valid edge — reject the candidate entirely.
        raw_edge_pct, raw_edge_was_capped = calculate_edge_pct_with_flag(true_price, yes_price, market_id)
        if raw_edge_was_capped:
            logger.info(
                "[EDGE] Rejecting %s: raw edge exceeds max sane edge %.2f%%",
                market_id, MAX_EDGE_PCT,
            )
            edge_or_profitability_blocked_count += 1
            if make_nearest_miss is not None:
                nearest_misses.append(make_nearest_miss(
                    market=market,
                    side="yes",
                    price=yes_price,
                    ai_prior=true_price,
                    raw_edge=raw_edge_pct,
                    fee_adjusted_edge=edge_after_fees_pct,
                    required_threshold=risk_caps["edge_after_fees_pct"],
                    rejection_reason="raw_edge_above_sanity_cap",
                ))
            _record_shadow_candidate(
                capture_runtime,
                market=market,
                observed_price=yes_price,
                ai_prior=true_price,
                fallback_prior_used=bool(market.get("_ai_prior_is_fallback")),
                raw_edge=raw_edge_pct,
                fee_adjusted_edge=edge_after_fees_pct,
                required_threshold=risk_caps["edge_after_fees_pct"],
                rejection_reason="raw_edge_above_sanity_cap",
                gate_failure_kinds=["price_sanity"],
                order_intent_created=False,
                expected_value=edge_after_fees_pct,
            )
            continue

        # Check if this market is a best maker market
        is_best_maker = (best_maker_market and market_id == best_maker_market.get("id"))

        # Only trade if edge after fees is sufficient
        if edge_after_fees_pct < risk_caps["edge_after_fees_pct"]:
            logger.debug(f"Market {market_id}: edge={edge_after_fees_pct:.2f}% < {risk_caps['edge_after_fees_pct']}%, too low")
            edge_or_profitability_blocked_count += 1
            if make_nearest_miss is not None:
                nearest_misses.append(make_nearest_miss(
                    market=market,
                    side="yes",
                    price=yes_price,
                    ai_prior=true_price,
                    raw_edge=raw_edge_pct,
                    fee_adjusted_edge=edge_after_fees_pct,
                    required_threshold=risk_caps["edge_after_fees_pct"],
                    rejection_reason="edge_below_threshold",
                ))
            _record_shadow_candidate(
                capture_runtime,
                market=market,
                observed_price=yes_price,
                ai_prior=true_price,
                fallback_prior_used=bool(market.get("_ai_prior_is_fallback")),
                raw_edge=raw_edge_pct,
                fee_adjusted_edge=edge_after_fees_pct,
                required_threshold=risk_caps["edge_after_fees_pct"],
                rejection_reason="edge_below_threshold",
                gate_failure_kinds=["edge_below_threshold"],
                order_intent_created=False,
                expected_value=edge_after_fees_pct,
            )
            continue
        
        # Determine if maker order (if not best maker)
        use_maker = not is_best_maker
        
        if use_maker and yes_price == 0.50:
            # Market at exactly 50¢ - maker order costs $0
            order_side = "yes"
            order_price = yes_price  # Buy at current price
            logger.info(f"Market {market_id}: YES order (maker) at {order_price:.4f} (fee: $0.00)")
            fee_cost = 0.0
        else:
            # Market not at 50¢ - maker order charges taker fee on fill
            # Use limit order just inside spread
            order_side = "yes"
            order_price = yes_price * 0.99  # Slightly below current price
            logger.info(f"Market {market_id}: YES order (limit) at {order_price:.4f} (will pay taker fee on fill)")
            # Estimate taker fee if filled: 0.7% of order_price
            # We'll pay taker fee only if our order is filled (someone crosses our spread)
            # We want to earn the spread (market maker), not cross it
            # If we're priced at 0.99 and someone crosses from 0.99 to 1.01, they get filled
            # But if we're the taker, we get the spread
            # Probability of being maker: Not 100%, but significant
            # For simplicity, assume we pay 0.7% taker fee 50% of the time (when our order fills)
            estimated_fee_pct = 0.035  # 0.07% taker fee / 2
            fee_cost = order_price * estimated_fee_pct / 100  # 0.99 * 0.00035
            
            logger.debug(f"Market {market_id}: Estimated fee: {estimated_fee_pct:.2f}% (${fee_cost:.4f})")
        order_intent_count += 1
        
        # Edge already computed by get_edge_after_fees above; do NOT recalculate
        # The fee-adjusted edge from get_edge_after_fees is the authoritative value
        logger.debug(f"Market {market_id}: Using edge_after_fees={edge_after_fees_pct:.2f}% from get_edge_after_fees")
        
        # Apply expiry penalty: >30d = 0.3x, >14d = 0.7x, <=7d = no penalty
        _days_to_exp = market.get("_days_to_end")
        if _days_to_exp is not None and _days_to_exp != float("inf"):
            if _days_to_exp > 30:
                edge_after_fees_pct *= 0.3
                logger.debug(f"Market {market_id}: {int(_days_to_exp)}d expiry — applied 0.3x long-dated penalty")
            elif _days_to_exp > 14:
                edge_after_fees_pct *= 0.7
                logger.debug(f"Market {market_id}: {int(_days_to_exp)}d expiry — applied 0.7x mid-dated penalty")
        
        # ── Price floor check (explicit, before gates) ──────────────────
        price_cents = int(round(yes_price * 100))
        if price_cents < MIN_TRADE_PRICE_CENTS:
            logger.info(
                "[PRICE] Skipping %s: price %dc < min %dc",
                market_id, price_cents, MIN_TRADE_PRICE_CENTS,
            )
            price_gate_blocked_count += 1
            if make_nearest_miss is not None:
                nearest_misses.append(make_nearest_miss(
                    market=market,
                    side=order_side,
                    price=yes_price,
                    ai_prior=true_price,
                    raw_edge=raw_edge_pct,
                    fee_adjusted_edge=edge_after_fees_pct,
                    required_threshold=risk_caps["edge_after_fees_pct"],
                    rejection_reason="price_below_minimum",
                ))
            _record_shadow_candidate(
                capture_runtime,
                market=market,
                observed_price=yes_price,
                ai_prior=true_price,
                fallback_prior_used=bool(market.get("_ai_prior_is_fallback")),
                raw_edge=raw_edge_pct,
                fee_adjusted_edge=edge_after_fees_pct,
                required_threshold=risk_caps["edge_after_fees_pct"],
                rejection_reason="price_below_minimum",
                gate_failure_kinds=["price_below_minimum"],
                order_intent_created=False,
                expected_value=edge_after_fees_pct,
            )
            continue

        # Check if order passes gates
        passed, violations = check_micro_live_gates(market, optimal_size, yes_price, risk_caps, "kalshi", computed_edge_pct=edge_after_fees_pct)
        
        if not passed:
            logger.debug(f"Market {market_id}: Failed gates: {violations}")
            gate_failure_kinds = gate_failure_kinds_from_violations(violations)
            if "edge_below_threshold" in gate_failure_kinds:
                edge_or_profitability_blocked_count += 1
                rejection_reason = "gate_edge_below_threshold"
            else:
                rejection_reason = "gate_failed"
            if make_nearest_miss is not None:
                nearest_misses.append(make_nearest_miss(
                    market=market,
                    side=order_side,
                    price=yes_price,
                    ai_prior=true_price,
                    raw_edge=raw_edge_pct,
                    fee_adjusted_edge=edge_after_fees_pct,
                    required_threshold=risk_caps["edge_after_fees_pct"],
                    rejection_reason=rejection_reason,
                    gate_failure_kinds=gate_failure_kinds,
                ))
            if scratchpad is not None:
                for v in violations:
                    if "minimum floor" in v:
                        scratchpad.log(
                            "gate_rejection",
                            ticker=market_id,
                            gate_name="min_price_floor",
                            price_cents=int(round(yes_price * 100)),
                            reason=v,
                        )
                    if "fallback prior" in v:
                        scratchpad.log(
                            "gate_rejection",
                            ticker=market_id,
                            gate_name="fallback_prior",
                            reason="cascade_failed",
                        )
            _record_shadow_candidate(
                capture_runtime,
                market=market,
                observed_price=yes_price,
                ai_prior=true_price,
                fallback_prior_used=bool(market.get("_ai_prior_is_fallback")),
                raw_edge=raw_edge_pct,
                fee_adjusted_edge=edge_after_fees_pct,
                required_threshold=risk_caps["edge_after_fees_pct"],
                rejection_reason=rejection_reason,
                gate_failure_kinds=gate_failure_kinds,
                order_intent_created=False,
                expected_value=edge_after_fees_pct,
            )
            continue

        _record_shadow_candidate(
            capture_runtime,
            market=market,
            observed_price=yes_price,
            ai_prior=true_price,
            fallback_prior_used=bool(market.get("_ai_prior_is_fallback")),
            raw_edge=raw_edge_pct,
            fee_adjusted_edge=edge_after_fees_pct,
            required_threshold=risk_caps["edge_after_fees_pct"],
            rejection_reason=None,
            gate_failure_kinds=[],
            order_intent_created=True,
            intent_price=order_price,
            maker_assumption="maker" if use_maker else "taker",
            expected_value=edge_after_fees_pct,
        )
        
        # Execute trade (in live modes: real-live or micro-live)
        # Track first-order safety countdown across the session
        if not hasattr(optimize_kalshi_strategy, "_first_order_placed"):
            optimize_kalshi_strategy._first_order_placed = False

        if is_live and not dry_run:
            prefix = MICRO_LIVE_LOG if mode == "micro-live" else ""

            # ── Dedup: skip markets with existing open orders ─────────────
            if not hasattr(optimize_kalshi_strategy, "_dedup_fetched"):
                optimize_kalshi_strategy._dedup_fetched = True
                try:
                    from utils.kalshi_orders import KalshiOrderClient
                    order_client_dedup = KalshiOrderClient()
                    existing_orders = order_client_dedup.get_orders(status="resting")
                    existing_positions = order_client_dedup.get_positions()
                except Exception as e:
                    logger.warning("%s Could not fetch existing orders/positions: %s", prefix, e)
                    existing_orders = []
                    existing_positions = []

                existing_order_tickers = {o.get("ticker") for o in existing_orders if o.get("ticker")}
                existing_position_tickers = {p.get("ticker") for p in existing_positions if p.get("ticker")}
                all_existing_tickers = existing_order_tickers | existing_position_tickers

                MAX_OPEN_ORDERS = 50
                if len(existing_orders) >= MAX_OPEN_ORDERS:
                    logger.warning(
                        "%s Already have %d open orders (max %d), skipping run",
                        prefix, len(existing_orders), MAX_OPEN_ORDERS,
                    )
                    submission_skipped_reason_counts["max_open_orders"] = submission_skipped_reason_counts.get("max_open_orders", 0) + 1
                    post_intent_blocker_counts["max_open_orders"] = post_intent_blocker_counts.get("max_open_orders", 0) + 1
                    break

                optimize_kalshi_strategy._existing_tickers = all_existing_tickers
                optimize_kalshi_strategy._existing_orders_count = len(existing_orders)

                logger.info(
                    "%s Existing open orders: %d, positions: %d",
                    prefix, len(existing_orders), len(existing_positions),
                )
            else:
                all_existing_tickers = getattr(optimize_kalshi_strategy, "_existing_tickers", set())

            if market_id in all_existing_tickers:
                logger.info("%s SKIP %s — already have open order or position", prefix, market_id)
                submission_skipped_reason_counts["duplicate_or_open_position"] = submission_skipped_reason_counts.get("duplicate_or_open_position", 0) + 1
                post_intent_blocker_counts["duplicate_or_open_position"] = post_intent_blocker_counts.get("duplicate_or_open_position", 0) + 1
                continue

            # ── Cash balance guard ─────────────────────────────────────────
            try:
                from utils.kalshi import get_kalshi_balance
                cash_balance = get_kalshi_balance()
                if cash_balance < 1.0:
                    logger.info(
                        "%s Insufficient cash: $%.2f, skipping order cycle", prefix, cash_balance
                    )
                    submission_skipped_reason_counts["insufficient_cash"] = submission_skipped_reason_counts.get("insufficient_cash", 0) + 1
                    post_intent_blocker_counts["insufficient_cash"] = post_intent_blocker_counts.get("insufficient_cash", 0) + 1
                    break
            except Exception as e:
                logger.warning("%s Cash balance check failed: %s", prefix, e)

            # ── Daily loss guard ────────────────────────────────────────────
            if is_live:
                daily_pnl = _get_todays_realized_pnl()
                if daily_pnl <= -MAX_DAILY_LOSS_USD:
                    logger.warning(
                        "[DAILY_LOSS] Skipping live order cycle: daily_pnl=%.2f max_loss=%.2f",
                        daily_pnl, MAX_DAILY_LOSS_USD,
                    )
                    submission_skipped_reason_counts["daily_loss_limit"] = submission_skipped_reason_counts.get("daily_loss_limit", 0) + 1
                    post_intent_blocker_counts["daily_loss_limit"] = post_intent_blocker_counts.get("daily_loss_limit", 0) + 1
                    break

            # ── Max orders per run ────────────────────────────────────────
            orders_placed_this_run = getattr(optimize_kalshi_strategy, "_orders_placed_this_run", 0)
            if orders_placed_this_run >= MAX_ORDERS_PER_RUN:
                logger.info(
                    "[RUN_LIMIT] Max orders per run reached: %d/%d",
                    orders_placed_this_run, MAX_ORDERS_PER_RUN,
                )
                submission_skipped_reason_counts["max_orders_per_run"] = submission_skipped_reason_counts.get("max_orders_per_run", 0) + 1
                post_intent_blocker_counts["max_orders_per_run"] = post_intent_blocker_counts.get("max_orders_per_run", 0) + 1
                break

            # ── Max notional per run ──────────────────────────────────────
            notional_this_run = getattr(optimize_kalshi_strategy, "_notional_this_run", 0.0)
            order_notional = order_price  # $ per contract
            if notional_this_run + order_notional > MAX_NOTIONAL_PER_RUN_USD:
                logger.info(
                    "[RUN_LIMIT] Max notional per run reached: %.2f/%.2f (next=%.2f)",
                    notional_this_run, MAX_NOTIONAL_PER_RUN_USD, order_notional,
                )
                submission_skipped_reason_counts["max_notional_per_run"] = submission_skipped_reason_counts.get("max_notional_per_run", 0) + 1
                post_intent_blocker_counts["max_notional_per_run"] = post_intent_blocker_counts.get("max_notional_per_run", 0) + 1
                break

            # Safety countdown on first order of session
            if not optimize_kalshi_strategy._first_order_placed:
                logger.warning(
                    "%s PLACING REAL ORDER in 3 seconds... Ctrl+C to abort", prefix
                )
                time.sleep(3)
                optimize_kalshi_strategy._first_order_placed = True

            # Convert price to cents (Kalshi native unit)
            price_cents = int(round(order_price * 100))
            quantity = 1  # KalshiOrderClient enforces MAX_QUANTITY=1

            try:
                from utils.kalshi_orders import KalshiOrderClient
                order_client = KalshiOrderClient()
                order_submission_attempted_count += 1
                result = order_client.place_order(
                    ticker=market_id,
                    side=order_side,
                    quantity=quantity,
                    price_cents=price_cents,
                )
                result_order = result.get("order", {})
                order_submission_succeeded_count += 1
                taker_cost_str = result_order.get("taker_fill_cost_dollars", "0")
                try:
                    cost_usd = float(taker_cost_str)
                except (ValueError, TypeError):
                    cost_usd = 0.0
                if cost_usd == 0.0:
                    cost_usd = price_cents / 100.0
                order_id = result_order.get("order_id", "unknown")

                logger.info(
                    "%s ORDER PLACED: %s %s @ %dc -> order_id=%s cost=$%.4f",
                    prefix, order_side, market_id, price_cents, order_id, cost_usd,
                )

                # Track orders and notional for run limits
                optimize_kalshi_strategy._orders_placed_this_run = getattr(optimize_kalshi_strategy, "_orders_placed_this_run", 0) + 1
                optimize_kalshi_strategy._notional_this_run = getattr(optimize_kalshi_strategy, "_notional_this_run", 0.0) + cost_usd

                # Write to proof pack — price_cents is int (cents), size_usd and cost_usd are float (dollars)
                proof_data.setdefault("orders_placed", []).append({
                    "market_id": market_id,
                    "side": order_side,
                    "price_cents": price_cents,     # INTEGER — price in cents (1-99)
                    "size_usd": optimal_size,       # FLOAT — position size in dollars
                    "cost_usd": cost_usd,           # FLOAT — actual fill cost in dollars
                    "quantity": quantity,
                    "result": result,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "mode": mode,
                    "ai_probability": true_price,
                    "ai_probability_raw": ai_raw_probability,
                    "vol_model_probability": vol_model_probability,
                    "calibrated_probability": calibrated_probability,
                    "calibration_bucket": calibration_bucket,
                    "calibration_trades": calibration_trades,
                    "vol_gate_min": vol_gate_min,
                    "blended_probability": true_price,
                    "edge_pct": edge_after_fees_pct,
                    "kelly_fraction": round(optimal_size / bankroll, 4) if bankroll > 0 else 0,
                    "expiration": market.get("close_time") or market.get("expiration_date"),
                    "days_to_expiry": market.get("_days_to_end"),
                    "liquidity_usd": market.get("liquidity_usd"),
                    "cascade_provider": market.get("_ai_tier", "unknown"),
                })
                _conf = market.get("_validation", {}).get("confidence")
                _ai_tier = market.get("_ai_tier", "unknown")
                try:
                    record_trade(
                        phase=mode, ticker=market_id, action="BUY",
                        price=order_price, size_usd=cost_usd,
                        confidence=_conf, signal_type=_ai_tier,
                        market_category=_extract_market_category(market_id),
                        order_type="limit",
                        ai_probability=true_price,
                        edge_pct=edge_after_fees_pct,
                        kelly_fraction=round(optimal_size / bankroll, 4) if bankroll > 0 else 0,
                        expiration=market.get("close_time") or market.get("expiration_date"),
                        days_to_expiry=market.get("_days_to_end"),
                        liquidity_usd=market.get("liquidity_usd"),
                        cascade_provider=market.get("_ai_tier", "unknown"),
                    )
                except Exception as exc:
                    logger.warning("%s record_trade failed after live order %s: %s", prefix, market_id, exc)
                # Discord notification for order placed
                if notify_order_placed:
                    try:
                        notify_order_placed(
                            ticker=market_id,
                            side=order_side,
                            price_cents=price_cents,
                            quantity=quantity,
                            ai_probability=ai_raw_probability,
                            vol_model_probability=vol_model_probability,
                            calibrated_probability=calibrated_probability,
                            blended_probability=true_price,
                            vol_gate_min=vol_gate_min,
                            edge_pct=edge_after_fees_pct,
                            kelly_fraction=round(optimal_size / bankroll, 4) if bankroll > 0 else 0,
                            cascade_provider=market.get("_ai_tier", "unknown"),
                            days_to_expiry=market.get("_days_to_end"),
                        )
                    except Exception:
                        pass
            except Exception as e:
                order_submission_failed_count += 1
                post_intent_blocker_counts["submission_failed"] = post_intent_blocker_counts.get("submission_failed", 0) + 1
                logger.error("%s ORDER FAILED: %s", prefix, e)
                proof_data.setdefault("orders_failed", []).append({
                    "market_id": market_id,
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        elif dry_run:
            submission_skipped_reason_counts["dry_run"] = submission_skipped_reason_counts.get("dry_run", 0) + 1
            post_intent_blocker_counts["dry_run"] = post_intent_blocker_counts.get("dry_run", 0) + 1
            prefix = MICRO_LIVE_LOG if mode == "micro-live" else "SHADOW MODE"
            price_cents_shadow = int(round(order_price * 100))
            cost_usd_shadow = price_cents_shadow / 100.0  # estimated cost in dollars
            logger.info(f"{prefix}: Would place order on {market_id}: {order_side} ${optimal_size:.2f} @ {order_price:.4f} (%dc, ~$%.2f)", price_cents_shadow, cost_usd_shadow)
            proof_data.setdefault("orders_placed", []).append({
                "market_id": market_id,
                "side": order_side,
                "price_cents": price_cents_shadow,
                "size_usd": optimal_size,
                "cost_usd": cost_usd_shadow,
                "quantity": 1,
                "result": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "ai_probability": true_price,
                "ai_probability_raw": ai_raw_probability,
                "vol_model_probability": vol_model_probability,
                "calibrated_probability": calibrated_probability,
                "calibration_bucket": calibration_bucket,
                "calibration_trades": calibration_trades,
                "vol_gate_min": vol_gate_min,
                "blended_probability": true_price,
                "edge_pct": edge_after_fees_pct,
                "kelly_fraction": round(optimal_size / bankroll, 4) if bankroll > 0 else 0,
                "expiration": market.get("close_time") or market.get("expiration_date"),
                "days_to_expiry": market.get("_days_to_end"),
                "liquidity_usd": market.get("liquidity_usd"),
                "cascade_provider": market.get("_ai_tier", "unknown"),
            })
            _conf = market.get("_validation", {}).get("confidence")
            _ai_tier = market.get("_ai_tier", "unknown")
            try:
                record_trade(
                    phase=mode, ticker=market_id, action="BUY",
                    price=order_price, size_usd=optimal_size,
                    confidence=_conf, signal_type=_ai_tier,
                    market_category=_extract_market_category(market_id),
                    order_type="limit",
                    ai_probability=true_price,
                    edge_pct=edge_after_fees_pct,
                    kelly_fraction=round(optimal_size / bankroll, 4) if bankroll > 0 else 0,
                    expiration=market.get("close_time") or market.get("expiration_date"),
                    days_to_expiry=market.get("_days_to_end"),
                    liquidity_usd=market.get("liquidity_usd"),
                    cascade_provider=market.get("_ai_tier", "unknown"),
                )
            except Exception as exc:
                logger.warning("%s record_trade failed for dry-run simulated order %s: %s", prefix, market_id, exc)
            logger.info("%s: Discord notification skipped for dry-run simulated order %s", prefix, market_id)

        # Update metrics
        total_trades += 1
        if use_maker and yes_price == 0.50:
            total_filled += 1  # Assume maker orders fill
        total_volume += optimal_size if is_live or dry_run else 0
        
        # Calculate expected profit
        if use_maker and yes_price == 0.50:
            # Maker order at 50¢: expected to win 50%
            expected_profit = optimal_size * 0.5  # 50% of order value
            logger.debug(f"Market {market_id}: Expected profit: ${expected_profit:.2f} (${expected_profit * 0.5:.2f} if win)")
        elif use_maker and yes_price < 0.50:
            # Maker order below 50¢: expected edge, taker fees
            expected_profit_pct = edge_after_fees_pct
            expected_profit = optimal_size * (expected_profit_pct / 100)
            logger.debug(f"Market {market_id}: Expected profit: {expected_profit_pct:.2f}% (${optimal_size * expected_profit_pct / 100:.2f} if win)")
        else:
            expected_profit = 0
        
        # Record order
        orders.append({
            "market": market_id,
            "side": order_side,
            "size": optimal_size,
            "price": order_price,
            "fee": fee_cost if "fee_cost" in locals() else 0.07 * yes_price / 100,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    
    # Summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Markets analyzed: {len(markets)}")
    logger.info(f"Best maker market: {best_maker_market.get('id') if best_maker_market else 'None'}")
    logger.info(f"Total orders placed: {total_trades}")
    logger.info(f"Total filled: {total_filled}")
    logger.info(f"Total volume: ${total_volume:.2f}")
    logger.info("=" * 60)
    
    # Generate proof
    proof_id = f"kalshi_optimized_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data.update({
        "data": {
            "orders": orders,
            "summary": {
                "total_orders": total_trades,
                "total_filled": total_filled,
                "total_volume": total_volume,
                "best_maker_market": best_maker_market.get("id") if best_maker_market else None
            }
        },
        "risk_caps": risk_caps
    })

    shadow_capture_summary = _flush_shadow_candidates(capture_runtime)

    should_write_edge_summary = bool(os.getenv("MAIN_EDGE_NEAREST_MISS_PATH")) or mode == "micro-live"
    if (
        should_write_edge_summary
        and build_edge_nearest_miss_summary is not None
        and write_edge_nearest_miss_summary is not None
    ):
        try:
            edge_summary = build_edge_nearest_miss_summary(
                run_timestamp=datetime.now(timezone.utc).isoformat(),
                ai_processed_count=cascade_attempted,
                order_intent_count=order_intent_count,
                nearest_misses=nearest_misses,
                edge_threshold=risk_caps["edge_after_fees_pct"],
                fee_adjusted_edge_threshold=risk_caps["edge_after_fees_pct"],
                no_profitable_maker_count=no_profitable_maker_count,
                price_gate_blocked_count=price_gate_blocked_count,
                edge_or_profitability_blocked_count=(
                    edge_or_profitability_blocked_count + no_profitable_maker_count
                ),
                order_placed_count=total_trades,
                order_submission_attempted_count=order_submission_attempted_count,
                order_submission_succeeded_count=order_submission_succeeded_count,
                order_submission_failed_count=order_submission_failed_count,
                submission_skipped_reason_counts=submission_skipped_reason_counts,
                post_intent_blocker_counts=post_intent_blocker_counts,
                sample_limit=10,
            )
            edge_summary.update(shadow_capture_summary)
            write_edge_nearest_miss_summary(edge_summary)
            logger.info(
                "[ORDER_DIAG] POST_INTENT_BLOCKER_COUNTS=%s",
                public_count_string(post_intent_blocker_counts) if public_count_string else "unknown",
            )
            logger.info(
                "[ORDER_DIAG] ORDER_INTENT_TO_SUBMISSION_STATUS=intents:%d,attempted:%d,succeeded:%d,failed:%d",
                order_intent_count,
                order_submission_attempted_count,
                order_submission_succeeded_count,
                order_submission_failed_count,
            )
            logger.info(
                "[ORDER_DIAG] SUBMISSION_SKIPPED_REASON_COUNTS=%s",
                public_count_string(submission_skipped_reason_counts) if public_count_string else "unknown",
            )
        except Exception as exc:
            logger.warning("[EDGE_DIAG] Could not write redacted nearest-miss summary: %s", exc)

    generate_proof(proof_id, proof_data)
    
    logger.info(f"Proof: {proof_id}")
    
    return 0, cascade_attempted, len(markets)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kalshi Optimization - Phase 1 (Quick Wins)")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow", help="Execution mode (micro-live = real trades with hard caps)")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=10.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    exit_code, _, _ = optimize_kalshi_strategy(
        mode=args.mode,
        bankroll=args.bankroll,
        max_pos_usd=args.max_pos,
        dry_run=(args.mode == "shadow")
    )
    
    logger.info(f"Exit code: {exit_code}")
    sys.exit(exit_code)
