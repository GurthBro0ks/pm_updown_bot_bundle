#!/usr/bin/env python3
"""
Autoresearch Scoring Harness
Computes composite score from shadow run proof files and pnl.db.

Score formula:
    composite = sharpe_ratio
                - (max_drawdown_pct * 2.0)
                - (turnover_rate * 0.5)
                + (simplicity_bonus * 0.3)

Usage:
    python3 scripts/autoresearch_scorer.py --proof-dir proofs/ --verbose
    python3 scripts/autoresearch_scorer.py --proof-dir proofs/ --holdout
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def get_db_path(proof_dir: str) -> str:
    """Find pnl.db relative to proof directory."""
    return os.path.join(os.path.dirname(proof_dir.rstrip('/')), 'paper_trading', 'pnl.db')


def load_proof_files(proof_dir: str) -> dict:
    """Load all relevant proof files and categorize by strategy."""
    proofs = {
        'kalshi': [],
        'stock_hunter': [],
        'sef': [],
        'crypto_spot': [],
        'shipping_mode': [],
        'gdelt': [],
    }

    proof_path = Path(proof_dir)
    if not proof_path.exists():
        return proofs

    for f in proof_path.glob('*.json'):
        name = f.name
        try:
            with open(f) as fp:
                data = json.load(fp)
        except (json.JSONDecodeError, IOError):
            continue

        if name.startswith('kalshi_optimized'):
            proofs['kalshi'].append(data)
        elif name.startswith('phase3_stock_hunter'):
            proofs['stock_hunter'].append(data)
        elif name.startswith('sef_spot') or name.startswith('sef_'):
            proofs['sef'].append(data)
        elif name.startswith('crypto_spot'):
            proofs['crypto_spot'].append(data)
        elif name.startswith('shipping_mode'):
            proofs['shipping_mode'].append(data)
        elif name.startswith('gdelt'):
            proofs['gdelt'].append(data)

    return proofs


def count_active_features(proofs: dict) -> int:
    """Count distinct signal sources that produced orders or data."""
    count = 0

    # Kalshi: check for orders
    for p in proofs.get('kalshi', []):
        orders = p.get('data', {}).get('orders', [])
        if orders:
            count += 1
            break

    # Stock Hunter: check for orders
    for p in proofs.get('stock_hunter', []):
        orders = p.get('data', {}).get('orders', [])
        if orders:
            count += 1
            break

    # SEF: check for orders (varies by proof format)
    for p in proofs.get('sef', []):
        orders = p.get('data', {}).get('orders', [])
        if orders:
            count += 1
            break

    # Crypto Spot: check for opportunities with orders
    for p in proofs.get('crypto_spot', []):
        opps = p.get('opportunities', [])
        if opps:
            count += 1
            break

    # GDELT: check for geo_risk_score output
    for p in proofs.get('gdelt', []):
        if p.get('geo_risk_score') is not None:
            count += 1
            break

    return count


def load_trades_from_db(db_path: str, holdout: bool = False) -> list:
    """Load trades from pnl.db, optionally filtered by holdout period."""
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT phase, ticker, action, price, size_usd, pnl_usd, pnl_pct, timestamp
            FROM trades
            ORDER BY timestamp ASC
        """)
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        rows = []

    conn.close()
    return [dict(r) for r in rows]


def load_equity_snapshots(db_path: str) -> list:
    """Load equity snapshots for drawdown calculation."""
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT cash, position_value, total_value, timestamp
            FROM equity_snapshots
            ORDER BY timestamp ASC
        """)
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        rows = []

    conn.close()
    return [dict(r) for r in rows]


def compute_sharpe_ratio(pnl_sequence: list) -> float:
    """
    Compute Sharpe ratio from sequence of PnL values.
    Returns 0.0 if insufficient data.
    """
    if len(pnl_sequence) < 2:
        return 0.0

    mean = sum(pnl_sequence) / len(pnl_sequence)
    variance = sum((x - mean) ** 2 for x in pnl_sequence) / len(pnl_sequence)
    std = math.sqrt(variance) if variance > 0 else 0.0

    if std == 0:
        return 0.0

    # Annualized Sharpe (assuming daily returns, 252 trading days)
    sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
    return sharpe


def compute_max_drawdown(equity_curve: list) -> float:
    """
    Compute max drawdown percentage from equity curve.
    Returns drawdown as positive percentage (e.g., 5.2 for 5.2% drawdown).
    """
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0

    for value in equity_curve:
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak if peak > 0 else 0.0
        if drawdown > max_dd:
            max_dd = drawdown

    return max_dd * 100.0  # Convert to percentage


def compute_turnover_rate(trades: list) -> float:
    """
    Compute turnover rate: trades per hour.
    """
    if not trades:
        return 0.0

    timestamps = [datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00')) for t in trades if t.get('timestamp')]
    if len(timestamps) < 2:
        return 0.0

    first = min(timestamps)
    last = max(timestamps)
    delta_hours = (last - first).total_seconds() / 3600

    if delta_hours <= 0:
        return 0.0

    return len(trades) / delta_hours


def score_strategy_run(proof_dir: str, holdout: bool = False) -> dict:
    """
    Reads all proof JSON files from a shadow run and computes a composite score.

    Args:
        proof_dir: Path to proofs directory
        holdout: If True, score against holdout set only

    Returns:
        dict with composite_score and all components
    """
    db_path = get_db_path(proof_dir)
    proofs = load_proof_files(proof_dir)

    # Load trades and equity data
    trades = load_trades_from_db(db_path, holdout=holdout)
    equity_snapshots = load_equity_snapshots(db_path)

    # Compute metrics
    total_trades = len([t for t in trades if t.get('action') in ('BUY', 'EXIT')])

    # Win rate: EXIT trades with positive PnL
    exit_trades = [t for t in trades if t.get('action') == 'EXIT']
    winning_trades = [t for t in exit_trades if t.get('pnl_usd', 0) > 0]
    win_rate = len(winning_trades) / len(exit_trades) if exit_trades else 0.0

    # Total PnL
    total_pnl = sum(t.get('pnl_usd', 0) for t in trades)

    # Equity curve for Sharpe and drawdown
    equity_curve = [s['total_value'] for s in equity_snapshots if s.get('total_value')]

    # Compute returns from equity curve
    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i-1] > 0:
            ret = (equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
            returns.append(ret)

    sharpe_ratio = compute_sharpe_ratio(returns) if returns else 0.0
    max_drawdown_pct = compute_max_drawdown(equity_curve) if equity_curve else 0.0

    # Turnover rate
    turnover_rate = compute_turnover_rate(trades)

    # Feature count (active signal sources)
    feature_count = count_active_features(proofs)
    simplicity_bonus = 1.0 / feature_count if feature_count > 0 else 0.0

    # Composite score formula
    composite = (
        sharpe_ratio
        - (max_drawdown_pct * 2.0)
        - (turnover_rate * 0.5)
        + (simplicity_bonus * 0.3)
    )

    return {
        'composite_score': round(composite, 4),
        'sharpe_ratio': round(sharpe_ratio, 4),
        'max_drawdown_pct': round(max_drawdown_pct, 4),
        'turnover_rate': round(turnover_rate, 4),
        'total_trades': total_trades,
        'win_rate': round(win_rate, 4),
        'total_pnl': round(total_pnl, 4),
        'feature_count': feature_count,
        'simplicity_bonus': round(simplicity_bonus, 4),
        'holdout': holdout,
        'timestamp': datetime.now().isoformat(),
    }


def print_score_breakdown(score_data: dict, verbose: bool = False):
    """Print formatted score breakdown."""
    print("=" * 50)
    print("AUTORESEARCH SCORE BREAKDOWN")
    print("=" * 50)
    print(f"  Composite Score:   {score_data['composite_score']:.4f}")
    print("-" * 50)
    print(f"  Sharpe Ratio:     {score_data['sharpe_ratio']:.4f}")
    print(f"  Max Drawdown %:   {score_data['max_drawdown_pct']:.4f}")
    print(f"  Turnover Rate:    {score_data['turnover_rate']:.4f} trades/hr")
    print(f"  Total Trades:     {score_data['total_trades']}")
    print(f"  Win Rate:         {score_data['win_rate']:.2%}")
    print(f"  Total PnL:        ${score_data['total_pnl']:.4f}")
    print(f"  Feature Count:    {score_data['feature_count']}")
    print(f"  Simplicity Bonus: {score_data['simplicity_bonus']:.4f}")
    print(f"  Holdout:          {score_data['holdout']}")
    print("-" * 50)
    print(f"  Timestamp:        {score_data['timestamp']}")
    print("=" * 50)

    if verbose:
        print("\nFORMULA:")
        print(f"  composite = sharpe_ratio")
        print(f"            - (max_drawdown_pct * 2.0)")
        print(f"            - (turnover_rate * 0.5)")
        print(f"            + (simplicity_bonus * 0.3)")
        print()
        print(f"  composite = {score_data['sharpe_ratio']:.4f}")
        print(f"            - ({score_data['max_drawdown_pct']:.4f} * 2.0)")
        print(f"            - ({score_data['turnover_rate']:.4f} * 0.5)")
        print(f"            + ({score_data['simplicity_bonus']:.4f} * 0.3)")
        print()


def main():
    parser = argparse.ArgumentParser(description='Autoresearch Scoring Harness')
    parser.add_argument('--proof-dir', default='proofs/', help='Path to proofs directory')
    parser.add_argument('--verbose', action='store_true', help='Print detailed breakdown')
    parser.add_argument('--holdout', action='store_true', help='Score against holdout set only')
    args = parser.parse_args()

    # Resolve proof_dir relative to bot root if needed
    proof_dir = args.proof_dir
    if not os.path.isabs(proof_dir):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        proof_dir = os.path.join(script_dir, '..', proof_dir)

    if not os.path.exists(proof_dir):
        print(f"ERROR: Proof directory not found: {proof_dir}")
        sys.exit(1)

    score_data = score_strategy_run(proof_dir, holdout=args.holdout)
    print_score_breakdown(score_data, verbose=args.verbose)

    # Output raw score for shell capture
    print(f"\nSCORE: {score_data['composite_score']:.4f}")


if __name__ == '__main__':
    main()
