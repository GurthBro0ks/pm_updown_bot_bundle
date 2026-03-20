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
        }

    pnls = [t["pnl_usd"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / len(pnls)
    mean_pnl = sum(pnls) / len(pnls)
    total_pnl = sum(pnls)

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

    # P-value: proportion of samples with win_rate <= 0.50
    p_value = sum(1 for wr in win_rates if wr <= 0.50) / n_iterations

    ci_win_rate = ci_95(win_rates)
    ci_mean_pnl = ci_95(mean_pnls)
    ci_sharpe = ci_95(sharpe_ratios) if sharpe_ratios else (0.0, 0.0)

    return {
        "observed_win_rate": observed["win_rate"],
        "observed_mean_pnl": observed["mean_pnl"],
        "observed_total_pnl": observed["total_pnl"],
        "observed_sharpe": observed["sharpe_ratio"],
        "ci_95_win_rate": [round(ci_win_rate[0], 4), round(ci_win_rate[1], 4)],
        "ci_95_mean_pnl": [round(ci_mean_pnl[0], 4), round(ci_mean_pnl[1], 4)],
        "ci_95_sharpe": [round(ci_sharpe[0], 4), round(ci_sharpe[1], 4)] if sharpe_ratios else [0.0, 0.0],
        "p_value_no_edge": round(p_value, 6),
    }


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def determine_verdict(total_trades: int, p_value: float, ci_win_rate: list[float], min_trades: int) -> tuple[str, str]:
    """Determine validation verdict based on bootstrap results."""
    if total_trades < min_trades:
        return "INSUFFICIENT_DATA", f"Only {total_trades} resolved trades found (minimum {min_trades} required)"

    if p_value < 0.05 and ci_win_rate[0] > 0.50:
        return "EDGE_CONFIRMED", "Win rate CI excludes 50% and p-value < 0.05 — edge is statistically significant"
    elif p_value < 0.05:
        return "NO_EDGE", "p-value < 0.05 but CI includes 50% — edge is not reliably positive"
    else:
        return "NO_EDGE", f"p-value {p_value:.4f} >= 0.05 — no statistically significant edge detected"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def build_report(
    trades: list[dict],
    bootstrap_result: dict,
    verdict: str,
    verdict_reason: str,
    n_iterations: int,
    source: str,
) -> dict:
    """Build the validation report dictionary."""
    return {
        "validator": "bootstrap_v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "total_trades": len(trades),
        "observed_win_rate": round(bootstrap_result["observed_win_rate"], 4),
        "observed_mean_pnl": round(bootstrap_result["observed_mean_pnl"], 4),
        "observed_total_pnl": round(bootstrap_result["observed_total_pnl"], 4),
        "observed_sharpe": round(bootstrap_result["observed_sharpe"], 4) if bootstrap_result["observed_sharpe"] is not None else None,
        "bootstrap_iterations": n_iterations,
        "ci_95_win_rate": bootstrap_result["ci_95_win_rate"],
        "ci_95_mean_pnl": bootstrap_result["ci_95_mean_pnl"],
        "ci_95_sharpe": bootstrap_result["ci_95_sharpe"],
        "p_value_no_edge": bootstrap_result["p_value_no_edge"],
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }


def print_human_report(report: dict):
    """Print a human-readable summary to stdout."""
    wr = report["observed_win_rate"]
    ci_wr = report["ci_95_win_rate"]
    ci_pnl = report["ci_95_mean_pnl"]
    ci_sh = report["ci_95_sharpe"]

    wr_str = f"{wr * 100:.1f}%"
    ci_wr_str = f"[{ci_wr[0] * 100:.1f}%, {ci_wr[1] * 100:.1f}%]"
    ci_pnl_str = f"[${ci_pnl[0]:.4f}, ${ci_pnl[1]:.4f}]"
    ci_sh_str = f"[{ci_sh[0]:.2f}, {ci_sh[1]:.2f}]"
    pval_str = f"{report['p_value_no_edge']:.4f}"

    sep = "=" * 60
    print()
    print(sep)
    print("BOOTSTRAP VALIDATION REPORT".center(60))
    print(sep)
    print(f"  Trades analyzed:     {report['total_trades']}")
    print(f"  Observed win rate:   {wr_str}")
    print(f"  95% CI win rate:     {ci_wr_str}")
    print(f"  95% CI mean PnL:     {ci_pnl_str}")
    print(f"  95% CI Sharpe:       {ci_sh_str}")
    print(f"  P-value (no edge):   {pval_str}")
    print(f"  Source:              {report['source']}")
    print(f"  Bootstrap iter:     {report['bootstrap_iterations']:,}")
    print()
    print(f"  Verdict:             {report['verdict']}")
    print(f"  Reason:              {report['verdict_reason']}")
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
        bootstrap_result["p_value_no_edge"],
        bootstrap_result["ci_95_win_rate"],
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
