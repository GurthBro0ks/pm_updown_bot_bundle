#!/usr/bin/env python3
"""
Bootstrap Trade Validator

Validates whether trading edge is statistically significant using bootstrap
resampling. Supports two data sources:
  - Source A: SQLite pnl.db (resolved trades table)
  - Source B: Proof JSON files in proofs/ dir

Usage:
  python3 bootstrap_validator.py                    # auto-detect data
  python3 bootstrap_validator.py --source db        # force SQLite
  python3 bootstrap_validator.py --source proofs    # force proof files
  python3 bootstrap_validator.py --min-trades 50    # override minimum
  python3 bootstrap_validator.py --iterations 50000 # more precision
"""

import argparse
import json
import math
import os
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROOF_DIR = Path("/opt/slimy/pm_updown_bot_bundle/proofs")
DB_PATH = "/opt/slimy/pm_updown_bot_bundle/paper_trading/pnl.db"
KALSHI_FEE_PCT = 0.07  # Kalshi fee on probability

# Bootstrap defaults
N_BOOTSTRAP_DEFAULT = 10000
MIN_TRADES_DEFAULT = 30


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_trades_from_db() -> list[dict]:
    """Load resolved trades (EXIT actions) from SQLite pnl.db."""
    trades = []
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT phase, ticker, action, price, size_usd, pnl_usd, pnl_pct, timestamp
            FROM trades
            WHERE action = 'EXIT'
            ORDER BY timestamp
        """).fetchall()
        conn.close()

        for r in rows:
            trades.append({
                "phase": r["phase"],
                "ticker": r["ticker"],
                "action": r["action"],
                "price": r["price"],
                "size_usd": r["size_usd"],
                "pnl_usd": r["pnl_usd"],
                "pnl_pct": r["pnl_pct"],
                "timestamp": r["timestamp"],
            })
    except Exception as e:
        print(f"[WARN] Could not load trades from DB: {e}", file=sys.stderr)

    return trades


def load_trades_from_proofs() -> list[dict]:
    """Load resolved trades from proof JSON files."""
    trades = []

    if not PROOF_DIR.exists():
        return trades

    # Only scan proof files that look like trade results (not test files)
    skip_patterns = ("test_", "validate_", "AGENTS", "yesno-integration")
    proof_files = [
        f for f in PROOF_DIR.iterdir()
        if f.is_file() and f.suffix == ".json" and not any(f.name.startswith(s) for s in skip_patterns)
    ]

    for proof_path in sorted(proof_files):
        try:
            with open(proof_path) as f:
                data = json.load(f)

            # Each proof file may contain a nested "trades" or "resolved" list
            # Look for trade outcome records inside the proof structure
            entries = (
                data.get("trades", []) or
                data.get("resolved", []) or
                data.get("positions", []) or
                []
            )

            for entry in entries:
                # Accept entries with at least pnl_usd or pnl field
                pnl = entry.get("pnl_usd") or entry.get("pnl")
                if pnl is None:
                    continue

                trades.append({
                    "phase": data.get("phase", "unknown"),
                    "ticker": entry.get("ticker", "UNKNOWN"),
                    "action": "EXIT",
                    "price": entry.get("exit_price") or entry.get("price", 0),
                    "size_usd": entry.get("size_usd") or entry.get("size", 0),
                    "pnl_usd": float(pnl),
                    "pnl_pct": entry.get("pnl_pct", 0),
                    "timestamp": entry.get("timestamp") or data.get("timestamp", ""),
                })
        except Exception:
            continue

    return trades


def calculate_kalshi_fee(price: float, size_usd: float) -> float:
    """
    Calculate Kalshi fee: 0.07 * price * (1 - price) * size_usd
    This approximates the fee on the notional value.
    """
    return KALSHI_FEE_PCT * price * (1 - price) * size_usd


def compute_pnl_metrics(trades: list[dict]) -> dict:
    """
    Compute observed metrics from a list of resolved trades.
    Each trade dict must have 'pnl_usd' and optionally 'pnl_pct'.
    """
    if not trades:
        return {
            "win_rate": 0.0,
            "mean_pnl": 0.0,
            "total_pnl": 0.0,
            "sharpe_ratio": None,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": None,
            "breakeven_win_rate": None,
        }

    pnls = [t["pnl_usd"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    mean_pnl = sum(pnls) / len(pnls) if pnls else 0.0
    total_pnl = sum(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    # Payoff ratio and breakeven win rate
    if avg_loss > 0 and avg_win > 0:
        payoff_ratio = avg_win / avg_loss
        breakeven_win_rate = 1.0 / (1.0 + payoff_ratio)
    else:
        payoff_ratio = None
        breakeven_win_rate = None

    # Sharpe ratio (annualized, assuming 1 trade/day, 252 trading days)
    if len(pnls) >= 2:
        returns = pnls  # treat each pnl as a return
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        if std_dev > 0:
            sharpe = (mean_ret / std_dev) * math.sqrt(252)
        else:
            sharpe = float("inf") if mean_ret > 0 else float("-inf")
    else:
        sharpe = None

    return {
        "win_rate": win_rate,
        "mean_pnl": mean_pnl,
        "total_pnl": total_pnl,
        "sharpe_ratio": sharpe,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "breakeven_win_rate": breakeven_win_rate,
    }


# ---------------------------------------------------------------------------
# Bootstrap resampling
# ---------------------------------------------------------------------------

def bootstrap_sample(trades: list[dict], rng: random.Random) -> list[dict]:
    """Resample trades with replacement."""
    n = len(trades)
    return [rng.choice(trades) for _ in range(n)]


def run_bootstrap(trades: list[dict], n_iterations: int, rng: random.Random) -> dict:
    """
    Run bootstrap resampling and compute confidence intervals.
    Returns dict with CI and p-value metrics.
    """
    observed = compute_pnl_metrics(trades)
    n = len(trades)

    win_rates = []
    mean_pnls = []
    sharpe_ratios = []

    for _ in range(n_iterations):
        sample = bootstrap_sample(trades, rng)
        metrics = compute_pnl_metrics(sample)
        win_rates.append(metrics["win_rate"])
        mean_pnls.append(metrics["mean_pnl"])
        if metrics["sharpe_ratio"] is not None:
            sharpe_ratios.append(metrics["sharpe_ratio"])

    def ci_95(values: list[float]) -> tuple[float, float]:
        """2.5th and 97.5th percentile confidence interval."""
        if not values:
            return (0.0, 0.0)
        sorted_vals = sorted(values)
        idx_low = int(0.025 * len(sorted_vals))
        idx_high = int(0.975 * len(sorted_vals)) - 1
        return (sorted_vals[idx_low], sorted_vals[idx_high])

    # P-value: proportion of samples with mean_pnl <= 0 (not profitable)
    p_value_not_profitable = sum(1 for mp in mean_pnls if mp <= 0) / n_iterations
    # Legacy p-value for win rate (kept for backward compatibility)
    p_value_no_edge = sum(1 for wr in win_rates if wr <= 0.50) / n_iterations

    ci_win_rate = ci_95(win_rates)
    ci_mean_pnl = ci_95(mean_pnls)
    ci_sharpe = ci_95(sharpe_ratios) if sharpe_ratios else (0.0, 0.0)

    return {
        "observed_win_rate": observed["win_rate"],
        "observed_mean_pnl": observed["mean_pnl"],
        "observed_total_pnl": observed["total_pnl"],
        "observed_sharpe": observed["sharpe_ratio"],
        "observed_avg_win": observed["avg_win"],
        "observed_avg_loss": observed["avg_loss"],
        "observed_payoff_ratio": observed["payoff_ratio"],
        "observed_breakeven_win_rate": observed["breakeven_win_rate"],
        "ci_95_win_rate": [round(ci_win_rate[0], 4), round(ci_win_rate[1], 4)],
        "ci_95_mean_pnl": [round(ci_mean_pnl[0], 4), round(ci_mean_pnl[1], 4)],
        "ci_95_sharpe": [round(ci_sharpe[0], 4), round(ci_sharpe[1], 4)] if sharpe_ratios else [0.0, 0.0],
        "p_value_no_edge": round(p_value_no_edge, 6),
        "p_value_not_profitable": round(p_value_not_profitable, 6),
    }


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def determine_verdict(
    total_trades: int,
    p_value_not_profitable: float,
    ci_mean_pnl: list[float],
    observed_mean_pnl: float,
    min_trades: int,
) -> tuple[str, str]:
    """Determine validation verdict based on EV-based bootstrap results."""
    if total_trades < min_trades:
        return "INSUFFICIENT_DATA", f"Only {total_trades} resolved trades found (minimum {min_trades} required)"

    # EDGE_CONFIRMED: p < 0.05 AND CI lower bound for mean_pnl > 0
    if p_value_not_profitable < 0.05 and ci_mean_pnl[0] > 0:
        reason = f"Mean PnL significantly > $0 (p={p_value_not_profitable:.4f}). "
        reason += f"95% CI [${ci_mean_pnl[0]:.4f}, ${ci_mean_pnl[1]:.4f}] excludes zero."
        return "EDGE_CONFIRMED", reason

    # MARGINAL_EDGE: p < 0.10 AND observed mean_pnl > 0 (but CI includes zero)
    if p_value_not_profitable < 0.10 and observed_mean_pnl > 0:
        reason = f"Mean PnL positive (${observed_mean_pnl:.4f}) but CI includes zero "
        reason += f"(p={p_value_not_profitable:.4f}). More data needed."
        return "MARGINAL_EDGE", reason

    # NO_EDGE
    if observed_mean_pnl <= 0:
        return "NO_EDGE", f"Mean PnL <= $0 (${observed_mean_pnl:.4f}). No profitable edge detected."
    else:
        return "NO_EDGE", f"p-value={p_value_not_profitable:.4f} >= 0.10. Mean PnL not significantly > $0."


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def classify_strategy(win_rate: float, payoff_ratio: float) -> str:
    """Classify strategy type based on win rate and payoff ratio."""
    if payoff_ratio is not None and payoff_ratio > 3 and win_rate < 0.35:
        return "asymmetric_payoff"
    if win_rate > 0.60 and payoff_ratio is not None and payoff_ratio < 1.5:
        return "high_frequency"
    return "balanced"


def build_report(
    trades: list[dict],
    bootstrap_result: dict,
    verdict: str,
    verdict_reason: str,
    n_iterations: int,
    source: str,
) -> dict:
    """Build the validation report dictionary."""
    win_rate = bootstrap_result["observed_win_rate"]
    payoff_ratio = bootstrap_result["observed_payoff_ratio"]
    breakeven_wr = bootstrap_result["observed_breakeven_win_rate"]
    excess_wr = (win_rate - breakeven_wr) if (breakeven_wr is not None and win_rate is not None) else None
    strategy_type = classify_strategy(win_rate, payoff_ratio)

    report = {
        "validator": "bootstrap_v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "total_trades": len(trades),
        "observed_win_rate": round(win_rate, 4),
        "observed_mean_pnl": round(bootstrap_result["observed_mean_pnl"], 4),
        "observed_total_pnl": round(bootstrap_result["observed_total_pnl"], 4),
        "observed_sharpe": round(bootstrap_result["observed_sharpe"], 4) if bootstrap_result["observed_sharpe"] is not None else None,
        "observed_avg_win": round(bootstrap_result["observed_avg_win"], 4),
        "observed_avg_loss": round(bootstrap_result["observed_avg_loss"], 4),
        "observed_payoff_ratio": round(payoff_ratio, 4) if payoff_ratio is not None else None,
        "observed_breakeven_win_rate": round(breakeven_wr, 4) if breakeven_wr is not None else None,
        "excess_win_rate": round(excess_wr, 4) if excess_wr is not None else None,
        "strategy_type": strategy_type,
        "bootstrap_iterations": n_iterations,
        "ci_95_win_rate": bootstrap_result["ci_95_win_rate"],
        "ci_95_mean_pnl": bootstrap_result["ci_95_mean_pnl"],
        "ci_95_sharpe": bootstrap_result["ci_95_sharpe"],
        "p_value_no_edge": bootstrap_result["p_value_no_edge"],
        "p_value_not_profitable": bootstrap_result["p_value_not_profitable"],
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }
    return report


def print_human_report(report: dict):
    """Print a human-readable summary to stdout."""
    wr = report["observed_win_rate"]
    mean_pnl = report["observed_mean_pnl"]
    total_pnl = report["observed_total_pnl"]
    avg_win = report["observed_avg_win"]
    avg_loss = report["observed_avg_loss"]
    payoff_ratio = report["observed_payoff_ratio"]
    breakeven_wr = report["observed_breakeven_win_rate"]
    excess_wr = report["excess_win_rate"]
    ci_wr = report["ci_95_win_rate"]
    ci_pnl = report["ci_95_mean_pnl"]
    ci_sh = report["ci_95_sharpe"]
    pval_np = report["p_value_not_profitable"]

    wr_str = f"{wr * 100:.1f}%"
    mean_pnl_str = f"${mean_pnl:.4f}/trade"
    total_pnl_str = f"${total_pnl:.2f}"
    avg_win_str = f"${avg_win:.2f}" if avg_win else "N/A"
    avg_loss_str = f"${avg_loss:.2f}" if avg_loss else "N/A"
    payoff_str = f"{payoff_ratio:.1f}:1" if payoff_ratio else "N/A"
    be_wr_str = f"{breakeven_wr * 100:.1f}%" if breakeven_wr else "N/A"
    excess_str = f"+{excess_wr * 100:.1f}%" if excess_wr else "N/A"
    ci_wr_str = f"[{ci_wr[0] * 100:.1f}%, {ci_wr[1] * 100:.1f}%]"
    ci_pnl_str = f"[${ci_pnl[0]:.4f}, ${ci_pnl[1]:.4f}]"
    ci_sh_str = f"[{ci_sh[0]:.2f}, {ci_sh[1]:.2f}]"
    pval_np_str = f"{pval_np:.4f}"

    sep = "=" * 60
    print()
    print(sep)
    print("BOOTSTRAP VALIDATION REPORT".center(60))
    print(sep)
    print(f"  Trades analyzed:       {report['total_trades']}")
    print(f"  Observed win rate:     {wr_str}")
    print(f"  Observed mean PnL:     {mean_pnl_str}")
    print(f"  Observed total PnL:    {total_pnl_str}")
    print(f"  Payoff ratio:          {payoff_str} (avg win {avg_win_str} / avg loss {avg_loss_str})")
    print(f"  Breakeven win rate:    {be_wr_str}")
    print(f"  Actual vs breakeven:   {wr_str} vs {be_wr_str} → {excess_str} excess")
    print(f"  ----------------------------------------")
    print(f"  95% CI win rate:       {ci_wr_str}")
    print(f"  95% CI mean PnL:       {ci_pnl_str}")
    print(f"  95% CI Sharpe:         {ci_sh_str}")
    print(f"  P-value (not profit):  {pval_np_str}")
    print(f"  ----------------------------------------")
    print(f"  Verdict:               {report['verdict']}")
    print(f"  Reason:                {report['verdict_reason']}")
    print(f"  Strategy type:          {report['strategy_type']}")
    print(sep)


def save_proof(report: dict) -> Path:
    """Save validation report as a JSON proof file."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"bootstrap_validation_{ts}.json"
    proof_path = PROOF_DIR / filename
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    with open(proof_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    return proof_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Bootstrap Trade Validator")
    parser.add_argument(
        "--source",
        choices=["db", "proofs", "auto"],
        default="auto",
        help="Data source: db (SQLite), proofs (JSON files), or auto (try db then proofs)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=MIN_TRADES_DEFAULT,
        help=f"Minimum trades required (default: {MIN_TRADES_DEFAULT})",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=N_BOOTSTRAP_DEFAULT,
        help=f"Number of bootstrap iterations (default: {N_BOOTSTRAP_DEFAULT})",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    min_trades = args.min_trades
    n_iterations = args.iterations

    # -------------------------------------------------------------------------
    # Load trades
    # -------------------------------------------------------------------------
    trades: list[dict] = []
    source: str = "unknown"

    if args.source in ("auto", "db"):
        trades = load_trades_from_db()
        source = "db"
        if trades:
            print(f"[INFO] Loaded {len(trades)} trades from SQLite DB", file=sys.stderr)

    if not trades and args.source in ("auto", "proofs"):
        trades = load_trades_from_proofs()
        source = "proofs"
        if trades:
            print(f"[INFO] Loaded {len(trades)} trades from proof files", file=sys.stderr)

    if not trades:
        print("[ERROR] No resolved trades found in DB or proof files", file=sys.stderr)
        # Still output an empty validation report
        report = {
            "validator": "bootstrap_v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": args.source,
            "total_trades": 0,
            "verdict": "INSUFFICIENT_DATA",
            "verdict_reason": "No resolved trades found",
        }
        proof_path = save_proof(report)
        print(f"[INFO] Proof saved to {proof_path}", file=sys.stderr)
        print_human_report(report)
        sys.exit(0)

    # -------------------------------------------------------------------------
    # Run bootstrap
    # -------------------------------------------------------------------------
    rng = random.Random(os.urandom(32))
    bootstrap_result = run_bootstrap(trades, n_iterations, rng)

    # -------------------------------------------------------------------------
    # Determine verdict
    # -------------------------------------------------------------------------
    verdict, verdict_reason = determine_verdict(
        len(trades),
        bootstrap_result["p_value_not_profitable"],
        bootstrap_result["ci_95_mean_pnl"],
        bootstrap_result["observed_mean_pnl"],
        min_trades,
    )

    # -------------------------------------------------------------------------
    # Build and save report
    # -------------------------------------------------------------------------
    report = build_report(trades, bootstrap_result, verdict, verdict_reason, n_iterations, source)
    proof_path = save_proof(report)

    print(f"[INFO] Proof saved to {proof_path}", file=sys.stderr)
    print_human_report(report)

    # Exit code 0 always (verdict is informational)
    sys.exit(0)


if __name__ == "__main__":
    main()
