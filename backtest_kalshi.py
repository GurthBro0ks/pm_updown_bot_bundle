#!/usr/bin/env python3
"""
Kalshi Vectorized Backtesting Harness

Reads resolved trade history from:
  1. paper_trading/pnl.db          — Polymarket/stock trades (for completeness)
  2. logs/scratchpad/prior_validation.jsonl — Kalshi signal records
  3. proofs/kalshi_optimized_*.json — Proof packs (synthetic mode fallback)

Calculates standardized metrics:
  - Total PnL ($)
  - Win rate (% of resolved trades that were profitable)
  - Sharpe ratio (annualised per-trade PnL)
  - Max drawdown (peak-to-trough on cumulative PnL curve)
  - Profit factor (gross_wins / gross_losses)
  - Average edge (mean entry price vs resolution price)
  - Days in market / total days

Outputs:
  - comparison_table() — stdout + JSON report
  - proofs/backtest_report_YYYYMMDD.json
  - proofs/backtest_equity_curve_YYYYMMDD.png (matplotlib agg backend)

CLI:
  python3 backtest_kalshi.py [--days N] [--config notes/experiment_*.json]
  python3 backtest_kalshi.py --synthetic [--proofs N]  # Monte Carlo simulation

Synthetic / Monte Carlo mode:
  Replays last N proof packs, simulates resolutions using signal probabilities,
  reports expected Sharpe range and drawdown confidence interval.
"""

import argparse
import json
import logging
import math
import os
import random
import re
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backtest_kalshi")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(os.environ.get("BASE_DIR", "/opt/slimy/pm_updown_bot_bundle"))
PROOF_DIR = BASE_DIR / "proofs"
LOG_DIR = BASE_DIR / "logs"
SCRATCHPAD_DIR = LOG_DIR / "scratchpad"
PNL_DB = BASE_DIR / "paper_trading" / "pnl.db"
NOTES_DIR = BASE_DIR / "notes"

# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_prior_validations(days: int = 30) -> list[dict]:
    """Load prior_validation records from scratchpad JSONL."""
    path = SCRATCHPAD_DIR / "prior_validation.jsonl"
    if not path.exists():
        logger.warning("No prior_validation.jsonl found at %s", path)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = rec.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if ts < cutoff:
                continue
            records.append(rec)
    logger.info("Loaded %d prior_validation records (last %d days)", len(records), days)
    return records


def load_proof_packs(limit: int = 20) -> list[dict]:
    """Load last N kalshi_optimized proof packs, newest first."""
    proof_files = sorted(
        PROOF_DIR.glob("kalshi_optimized_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    packs = []
    for pf in proof_files:
        try:
            with open(pf) as f:
                packs.append(json.load(f))
        except Exception as e:
            logger.warning("Failed to load %s: %s", pf.name, e)
    logger.info("Loaded %d proof packs", len(packs))
    return packs


def load_pnl_db_trades(days: int = 30) -> list[dict]:
    """Load trades from paper_trading/pnl.db (Polymarket/stock style)."""
    if not PNL_DB.exists():
        logger.warning("pnl.db not found at %s", PNL_DB)
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(PNL_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp",
        (cutoff,),
    ).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    logger.info("Loaded %d trades from pnl.db (last %d days)", len(trades), days)
    return trades


# ---------------------------------------------------------------------------
# Synthetic trade reconstruction from proof packs + scratchpad
# ---------------------------------------------------------------------------

def reconstruct_synthetic_trades(
    proof_packs: list[dict],
    prior_records: list[dict],
    seed: int = 42,
    silent: bool = False,
) -> list[dict]:
    """
    Reconstruct a list of synthetic trades from proof packs.

    Each proof pack contains `data.orders` (list of {market, side, price, size, timestamp}).
    We match each order to its prior_validation record to get the signal probability,
    then simulate resolution via Bernoulli trial using that probability.

    If proof packs have no orders (shadow mode), falls back to generating trades
    directly from prior_validation records using the signal probability as entry price.

    Pass silent=True to suppress info logs (used inside MC loops).

    Returns list of synthetic trade dicts:
      {market, side, entry_price, contracts, pnl, resolved, timestamp, prob}
    """
    rng = random.Random(seed)

    # Build a lookup: market_id -> prior record (most recent)
    prior_map: dict[str, dict] = {}
    for rec in sorted(prior_records, key=lambda r: r.get("ts", ""), reverse=True):
        market = rec.get("market", "")
        if market and market not in prior_map:
            prior_map[market] = rec

    trades = []
    for pack in proof_packs:
        orders = pack.get("data", {}).get("orders", [])
        for order in orders:
            market = order.get("market", "")
            side = order.get("side", "yes")  # yes / no
            entry_price = float(order.get("price", 0))
            contracts = float(order.get("size", 0))  # size in USD
            timestamp = order.get("timestamp", "")

            if entry_price <= 0 or contracts <= 0:
                continue

            # Get signal probability from prior record
            prior_rec = prior_map.get(market, {})
            prob = float(prior_rec.get("prior", prior_rec.get("adjusted_prior", 0.5)))

            # Simulate resolution: win if random() < prob AND side matches
            # For YES side: win if market resolves YES (prob), lose if NO (1-prob)
            # For NO side: win if market resolves NO (1-prob), lose if YES (prob)
            roll = rng.random()
            if side.lower() == "yes":
                won = roll < prob
            else:
                won = roll >= prob

            if won:
                pnl = (1.00 - entry_price) * contracts
            else:
                pnl = -entry_price * contracts

            trades.append({
                "market": market,
                "side": side,
                "entry_price": entry_price,
                "contracts": contracts,
                "pnl": pnl,
                "won": won,
                "resolved": True,
                "timestamp": timestamp,
                "prob": prob,
            })

    # If no orders from proof packs (shadow mode), generate synthetic trades
    # directly from prior_validation records
    if not trades and prior_records:
        if not silent:
            logger.info("Proof packs have no orders — generating synthetic trades from prior_validation records")
        trades = synthetic_trades_from_prior(prior_records, prior_map, rng)

    if not silent:
        logger.info("Reconstructed %d synthetic trades from %d proof packs + prior records",
                    len(trades), len(proof_packs))
    return trades


def synthetic_trades_from_prior(
    prior_records: list[dict],
    prior_map: dict[str, dict],
    rng: random.Random,
    size_usd: float = 1.0,
) -> list[dict]:
    """
    Generate synthetic trades from prior_validation records.

    Each record represents a market where the signal passed validation.
    We treat the `prior` (or adjusted_prior) as the entry price and signal probability,
    then simulate resolution via Bernoulli trial.

    Entry price = prior value (this is what you'd pay on Kalshi at that probability)
    Contracts = size_usd (default $1 per trade for uniform sizing)

    Returns list of synthetic trade dicts.
    """
    trades = []
    for rec in prior_records:
        market = rec.get("market", "")
        if not market or market in ("TEST",) or market.startswith("TEST-"):
            continue

        prior = float(rec.get("prior", rec.get("adjusted_prior", 0.5)))
        ts = rec.get("ts", "")
        flags = rec.get("flags", [])
        passed = rec.get("passed", False)

        if not passed:
            continue

        # Skip markets that are too close to 50% (no edge)
        if abs(prior - 0.5) < 0.02:
            continue

        # Entry price is the Kalshi price corresponding to the probability
        entry_price = prior
        contracts = size_usd

        # Determine side: YES if prior > 0.5, NO if prior < 0.5
        side = "yes" if prior > 0.5 else "no"

        # Simulate resolution
        roll = rng.random()
        won = roll < prior if side == "yes" else roll >= (1 - prior)

        if won:
            pnl = (1.00 - entry_price) * contracts
        else:
            pnl = -entry_price * contracts

        trades.append({
            "market": market,
            "side": side,
            "entry_price": entry_price,
            "contracts": contracts,
            "pnl": pnl,
            "won": won,
            "resolved": True,
            "timestamp": ts,
            "prob": prior,
            "flags": flags,
        })

    return trades


# ---------------------------------------------------------------------------
# Monte Carlo simulation
# ---------------------------------------------------------------------------

def monte_carlo_trades(
    proof_packs: list[dict],
    prior_records: list[dict],
    n_simulations: int = 1000,
    seed: int = 42,
) -> list[list[dict]]:
    """
    Run Monte Carlo simulation of resolutions across proof pack trades.
    Returns n_simulations copies of the reconstructed trade list with
    different random resolution outcomes.
    """
    rng = random.Random(seed)
    sims = []
    for i in range(n_simulations):
        rng.seed(seed + i)
        trades = reconstruct_synthetic_trades(proof_packs, prior_records, seed=seed + i)
        sims.append(trades)
    return sims


# ---------------------------------------------------------------------------
# Metrics calculation
# ---------------------------------------------------------------------------

def calc_metrics(trades: list[dict], trading_days: Optional[int] = None) -> dict:
    """
    Calculate standardised backtest metrics from a list of resolved trades.

    Each trade must have: pnl (float), won (bool), timestamp (str)
    """
    if not trades:
        return {
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
            "avg_edge": 0.0,
            "days_in_market": 0,
            "total_days": 1,
            "n_trades": 0,
            "n_wins": 0,
            "n_losses": 0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "ci_95_sharpe": [0.0, 0.0],
            "ci_95_max_dd": [0.0, 0.0],
        }

    pnls = [t["pnl"] for t in trades]
    wins = [t["pnl"] for t in trades if t.get("won", False)]
    losses = [t["pnl"] for t in trades if not t.get("won", False)]

    total_pnl = sum(pnls)
    n_trades = len(trades)
    n_wins = len(wins)
    n_losses = len(losses)

    win_rate = n_wins / n_trades if n_trades else 0.0
    gross_wins = sum(w for w in wins)
    gross_losses = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    avg_win = sum(wins) / n_wins if n_wins else 0.0
    avg_loss = sum(losses) / n_losses if n_losses else 0.0

    # Average edge: mean difference between entry_price and 1.0 (full payout)
    # For winning trades, edge = 1.0 - entry_price; for losing, edge = 0 - entry_price
    avg_edge = sum(
        (1.0 - t["entry_price"]) if t.get("won") else (-t["entry_price"])
        for t in trades
    ) / n_trades if n_trades else 0.0

    # Sharpe ratio (annualised)
    mean_pnl = sum(pnls) / n_trades if n_trades else 0.0
    # Population std
    if n_trades > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / n_trades
        std_pnl = math.sqrt(variance)
    else:
        std_pnl = 1.0  # avoid division by zero

    if trading_days is None:
        # Estimate from timestamps
        timestamps = [t.get("timestamp", "") for t in trades if t.get("timestamp")]
        if len(timestamps) >= 2:
            try:
                ts0 = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
                ts1 = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
                delta_days = max((ts1 - ts0).days, 1)
            except Exception:
                delta_days = 1
        else:
            delta_days = 1
    else:
        delta_days = max(trading_days, 1)

    trades_per_day = n_trades / delta_days if delta_days > 0 else 1
    # Annualise by sqrt(trades_per_day * 252)
    annualisation = math.sqrt(trades_per_day * 252)
    sharpe = (mean_pnl / std_pnl) * annualisation if std_pnl > 0 else 0.0

    # Max drawdown on cumulative PnL curve
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_dd:
            max_dd = drawdown

    # Days in market: count calendar days that have at least one trade
    trade_days = set()
    for t in trades:
        ts_str = t.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                trade_days.add(ts.date().isoformat())
            except Exception:
                pass

    return {
        "total_pnl": round(total_pnl, 4),
        "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999.999,
        "avg_edge": round(avg_edge, 4),
        "days_in_market": len(trade_days),
        "total_days": delta_days,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "ci_95_sharpe": [0.0, 0.0],  # filled by mc_analysis
        "ci_95_max_dd": [0.0, 0.0],  # filled by mc_analysis
    }


def mc_analysis(
    proof_packs: list[dict],
    prior_records: list[dict],
    n_sims: int = 500,
    seed: int = 42,
) -> dict:
    """
    Monte Carlo analysis: run n_sims simulations, return CI for Sharpe and max_dd.
    """
    sharpes = []
    max_dds = []
    for i in range(n_sims):
        trades = reconstruct_synthetic_trades(
            proof_packs, prior_records, seed=seed + i, silent=True
        )
        m = calc_metrics(trades)
        sharpes.append(m["sharpe"])
        max_dds.append(m["max_drawdown"])

    sharpes.sort()
    max_dds.sort()
    n = len(sharpes)
    if n == 0:
        return {"ci_95_sharpe": [0.0, 0.0], "ci_95_max_dd": [0.0, 0.0]}
    ci_95_sharpe = [
        round(sharpes[int(n * 0.025)], 4),
        round(sharpes[int(n * 0.975)], 4),
    ]
    ci_95_max_dd = [
        round(max_dds[int(n * 0.025)], 4),
        round(max_dds[int(n * 0.975)], 4),
    ]
    logger.info("MC(%d sims): Sharpe 95%% CI = %s, MaxDD 95%% CI = %s",
                n_sims, ci_95_sharpe, ci_95_max_dd)
    return {
        "ci_95_sharpe": ci_95_sharpe,
        "ci_95_max_dd": ci_95_max_dd,
    }


# ---------------------------------------------------------------------------
# Equity curve plotting
# ---------------------------------------------------------------------------

def plot_equity_curve(trades: list[dict], output_path: Path) -> None:
    """Plot cumulative PnL curve and save as PNG (agg backend)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available — skipping equity curve plot")
        return

    if not trades:
        logger.warning("No trades — skipping equity curve")
        return

    # Sort by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", ""))

    cumulative = []
    timestamps = []
    running = 0.0
    for t in sorted_trades:
        running += t["pnl"]
        cumulative.append(running)
        ts_str = t.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                timestamps.append(ts)
            except Exception:
                timestamps.append(None)
        else:
            timestamps.append(None)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(cumulative, color="#00d4ff", linewidth=1.5)
    ax.fill_between(range(len(cumulative)), cumulative, alpha=0.15, color="#00d4ff")
    ax.axhline(0, color="white", linewidth=0.5, linestyle="--")
    ax.set_title("Kalshi Backtest — Cumulative PnL", color="white")
    ax.set_xlabel("Trade #", color="white")
    ax.set_ylabel("Cumulative PnL ($)", color="white")
    ax.tick_params(colors="white")
    ax.set_facecolor("#0d1117")
    fig.patch.set_facecolor("#0d1117")
    ax.grid(True, alpha=0.1)

    # Mark max drawdown
    running = 0.0
    peak = 0.0
    max_dd_idx = 0
    for i, t in enumerate(sorted_trades):
        running += t["pnl"]
        if running > peak:
            peak = running
        if peak - running > 0:
            max_dd_idx = i  # approximate

    # Save
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, facecolor="#0d1117")
    plt.close()
    logger.info("Equity curve saved to %s", output_path)


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

def comparison_table(results: list[dict]) -> str:
    """
    Print a comparison table for multiple backtest configs.
    results: list of dicts with keys: name, total_pnl, win_rate, sharpe,
             max_drawdown, profit_factor, n_trades, days_in_market, total_days
    """
    if not results:
        return "No results to compare."

    header = (
        f"{'Config':<20} {'PnL ($)':>10} {'Win%':>7} {'Sharpe':>8} "
        f"{'MaxDD':>8} {'PF':>8} {'Trades':>7} {'Days':>6} {'MktDays':>8}"
    )
    divider = "-" * len(header)
    lines = [header, divider]
    for r in results:
        name = r.get("name", "unknown")[:20]
        pnl = r.get("total_pnl", 0)
        wr = r.get("win_rate", 0)
        sharpe = r.get("sharpe", 0)
        dd = r.get("max_drawdown", 0)
        pf = r.get("profit_factor", 0)
        n = r.get("n_trades", 0)
        days = r.get("total_days", 0)
        mkt_days = r.get("days_in_market", 0)
        lines.append(
            f"{name:<20} {pnl:>10.4f} {wr:>7.1%} {sharpe:>8.4f} "
            f"{dd:>8.4f} {pf:>8.4f} {n:>7} {days:>6} {mkt_days:>8}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main backtest entry point
# ---------------------------------------------------------------------------

def backtest_from_proofs(
    proof_packs: list[dict],
    prior_records: list[dict],
    name: str = "synthetic",
    run_mc: bool = False,
    mc_sims: int = 500,
) -> dict:
    """Run backtest from proof packs + prior records. Returns metrics dict."""
    trades = reconstruct_synthetic_trades(proof_packs, prior_records)
    if not trades:
        logger.warning("No trades reconstructed — check proof packs and prior records")
        return {}

    metrics = calc_metrics(trades)

    if run_mc:
        mc = mc_analysis(proof_packs, prior_records, n_sims=mc_sims)
        metrics["ci_95_sharpe"] = mc["ci_95_sharpe"]
        metrics["ci_95_max_dd"] = mc["ci_95_max_dd"]

    metrics["name"] = name
    return metrics


def backtest_from_pnl_db(days: int = 30) -> dict:
    """Run backtest from paper_trading/pnl.db (Polymarket/stock trades)."""
    trades_raw = load_pnl_db_trades(days)
    if not trades_raw:
        return {}

    # Convert to generic trade format
    trades = []
    for t in trades_raw:
        action = t.get("action", "").upper()
        pnl = float(t.get("pnl_usd", 0))
        if action == "EXIT" and pnl != 0:
            trades.append({
                "market": t.get("ticker", ""),
                "side": "long",  # Polymarket style
                "entry_price": float(t.get("price", 0)),
                "contracts": float(t.get("size_usd", 0)),
                "pnl": pnl,
                "won": pnl > 0,
                "resolved": True,
                "timestamp": t.get("timestamp", ""),
                "prob": 0.5,
            })
    return calc_metrics(trades)


def write_report(metrics: dict, output_dir: Path = PROOF_DIR) -> Path:
    """Write JSON report to proofs/backtest_report_YYYYMMDD.json."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = output_dir / f"backtest_report_{today}.json"
    # Serializable copy
    report = {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
              for k, v in metrics.items()}
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Report written to %s", path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kalshi Vectorized Backtest Harness")
    parser.add_argument("--days", type=int, default=30, help="Days of history to analyse")
    parser.add_argument("--proofs", type=int, default=20, help="Number of recent proof packs to load")
    parser.add_argument("--synthetic", action="store_true", help="Force synthetic mode (proof packs + MC)")
    parser.add_argument("--mc-sims", type=int, default=500, help="Monte Carlo simulations")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--config", type=str, help="Autoresearch config file (notes/experiment_*.json)")
    parser.add_argument("--output-json", action="store_true", help="Also print JSON report to stdout")
    args = parser.parse_args()

    random.seed(args.seed)

    prior_records = load_prior_validations(days=args.days)
    proof_packs = load_proof_packs(limit=args.proofs)

    results = []

    # 1. Synthetic mode: from proof packs
    if proof_packs or args.synthetic:
        logger.info("=== Synthetic backtest from proof packs ===")
        metrics = backtest_from_proofs(
            proof_packs, prior_records,
            name="synthetic_proofpacks",
            run_mc=True,
            mc_sims=args.mc_sims,
        )
        if metrics:
            results.append(metrics)
            print("\n" + comparison_table(results))
            if args.output_json:
                print(json.dumps(metrics, indent=2, default=str))

            # Plot + write report
            trades = reconstruct_synthetic_trades(proof_packs, prior_records, seed=args.seed)
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            plot_path = PROOF_DIR / f"backtest_equity_curve_{today}.png"
            plot_equity_curve(trades, plot_path)
            write_report(metrics)

    # 2. pnl.db backtest (Polymarket/stock — for completeness)
    logger.info("=== pnl.db backtest ===")
    pnl_metrics = backtest_from_pnl_db(days=args.days)
    if pnl_metrics:
        pnl_metrics["name"] = "pnl_db"
        results.append(pnl_metrics)
        print("\n" + comparison_table(results))

    if not results:
        logger.error("No data found. Check:\n  1. proofs/kalshi_optimized_*.json\n  2. logs/scratchpad/prior_validation.jsonl\n  3. paper_trading/pnl.db")
        sys.exit(1)

    print("\n=== Comparison Table ===")
    print(comparison_table(results))
    sys.exit(0)


if __name__ == "__main__":
    main()
