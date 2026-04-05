#!/usr/bin/env python3
"""
Experiment Logger for Autoresearch
Maintains experiments.tsv with all optimization experiments.

Columns:
    exp_id | timestamp | hypothesis | file_changed | change_type |
    score_before | score_after | delta | kept | git_hash | notes

Usage:
    python3 scripts/experiment_log.py --summary
    python3 scripts/experiment_log.py --log "Added feature X" --file strategies/foo.py --type feature_add --before 1.5 --after 1.8
"""

import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


EXPERIMENTS_FILE = 'experiments.tsv'
CHANGE_TYPES = ['param_tune', 'feature_add', 'feature_remove', 'logic_change']


def get_git_hash() -> str:
    """Get current git commit hash."""
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except subprocess.CalledProcessError:
        return 'unknown'


def get_git_diff() -> str:
    """Get list of changed files."""
    try:
        result = subprocess.check_output(
            ['git', 'diff', '--name-only', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return result if result else 'none'
    except subprocess.CalledProcessError:
        return 'unknown'


def ensure_tsv() -> str:
    """Ensure experiments.tsv exists with headers. Returns path."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tsv_path = os.path.join(script_dir, '..', EXPERIMENTS_FILE)

    if not os.path.exists(tsv_path):
        with open(tsv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'exp_id', 'timestamp', 'hypothesis', 'file_changed',
                'change_type', 'score_before', 'score_after', 'delta',
                'kept', 'git_hash', 'notes'
            ])
            writer.writeheader()

    return tsv_path


def get_next_exp_id(tsv_path: str) -> int:
    """Get next experiment ID by counting existing rows."""
    if not os.path.exists(tsv_path):
        return 1
    with open(tsv_path) as f:
        reader = csv.DictReader(f, delimiter='\t')
        rows = list(reader)
    if not rows:
        return 1
    max_id = max(int(r['exp_id']) for r in rows if r.get('exp_id', '').isdigit())
    return max_id + 1


def log_experiment(
    hypothesis: str,
    file_changed: str,
    change_type: str,
    score_before: float,
    score_after: float,
    kept: bool,
    git_hash: Optional[str] = None,
    notes: str = ''
) -> dict:
    """
    Log a single experiment to the TSV file.

    Returns the logged experiment dict.
    """
    if change_type not in CHANGE_TYPES:
        raise ValueError(f"Invalid change_type: {change_type}. Must be one of {CHANGE_TYPES}")

    tsv_path = ensure_tsv()
    exp_id = get_next_exp_id(tsv_path)
    timestamp = datetime.now().isoformat()
    delta = score_after - score_before
    kept_str = 'yes' if kept else 'no'

    if git_hash is None:
        git_hash = get_git_hash()

    experiment = {
        'exp_id': exp_id,
        'timestamp': timestamp,
        'hypothesis': hypothesis,
        'file_changed': file_changed,
        'change_type': change_type,
        'score_before': f'{score_before:.4f}',
        'score_after': f'{score_after:.4f}',
        'delta': f'{delta:.4f}',
        'kept': kept_str,
        'git_hash': git_hash,
        'notes': notes,
    }

    with open(tsv_path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=experiment.keys(), delimiter='\t')
        writer.writerow(experiment)

    return experiment


def get_experiment_history() -> list:
    """Read all experiments from TSV. Returns list of dicts."""
    tsv_path = ensure_tsv()
    if not os.path.exists(tsv_path):
        return []

    with open(tsv_path) as f:
        reader = csv.DictReader(f, delimiter='\t')
        return list(reader)


def get_best_score() -> float:
    """Get the best (highest) composite score achieved."""
    history = get_experiment_history()
    if not history:
        return 0.0
    scores = [float(r['score_after']) for r in history if r.get('score_after')]
    return max(scores) if scores else 0.0


def get_removal_stats() -> dict:
    """
    Track removal vs addition win rates.
    Returns dict with feature_add, feature_remove, param_tune, logic_change counts and kept rates.
    """
    history = get_experiment_history()
    if not history:
        return {
            'feature_add': {'total': 0, 'kept': 0, 'rate': 0.0},
            'feature_remove': {'total': 0, 'kept': 0, 'rate': 0.0},
            'param_tune': {'total': 0, 'kept': 0, 'rate': 0.0},
            'logic_change': {'total': 0, 'kept': 0, 'rate': 0.0},
        }

    stats = {ct: {'total': 0, 'kept': 0} for ct in CHANGE_TYPES}

    for r in history:
        ct = r.get('change_type', '')
        if ct in stats:
            stats[ct]['total'] += 1
            if r.get('kept', '').lower() == 'yes':
                stats[ct]['kept'] += 1

    for ct in stats:
        total = stats[ct]['total']
        kept = stats[ct]['kept']
        stats[ct]['rate'] = kept / total if total > 0 else 0.0

    return stats


def print_summary():
    """Print experiment summary to stdout."""
    history = get_experiment_history()
    total = len(history)

    if total == 0:
        print("=" * 50)
        print("EXPERIMENT LOG SUMMARY")
        print("=" * 50)
        print("  Total experiments:  0")
        print("  No experiments logged yet.")
        print("=" * 50)
        return

    kept_count = sum(1 for r in history if r.get('kept', '').lower() == 'yes')
    kept_pct = (kept_count / total * 100) if total > 0 else 0

    best = get_best_score()

    # Average delta
    deltas = []
    for r in history:
        if r.get('delta', '').replace('.', '').replace('-', '').isdigit():
            deltas.append(float(r['delta']))
    avg_delta = sum(deltas) / len(deltas) if deltas else 0.0

    # Removal stats
    removal_stats = get_removal_stats()

    print("=" * 50)
    print("EXPERIMENT LOG SUMMARY")
    print("=" * 50)
    print(f"  Total experiments:  {total}")
    print(f"  Kept:               {kept_count} ({kept_pct:.1f}%)")
    print(f"  Best score:         {best:.4f}")
    print(f"  Avg delta:          {avg_delta:+.4f}")
    print("-" * 50)
    print("  By change type:")
    for ct in CHANGE_TYPES:
        s = removal_stats.get(ct, {'total': 0, 'kept': 0, 'rate': 0.0})
        if s['total'] > 0:
            print(f"    {ct:15s}: {s['total']:3d} total, {s['kept']:3d} kept ({s['rate']:.1%})")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description='Experiment Logger for Autoresearch')
    parser.add_argument('--summary', action='store_true', help='Print summary statistics')
    parser.add_argument('--log', type=str, help='Hypothesis/description of experiment')
    parser.add_argument('--file', type=str, help='File changed')
    parser.add_argument('--type', type=str, choices=CHANGE_TYPES, help='Change type')
    parser.add_argument('--before', type=float, help='Score before change')
    parser.add_argument('--after', type=float, help='Score after change')
    parser.add_argument('--kept', action='store_true', help='Experiment kept (score improved)')
    parser.add_argument('--notes', type=str, default='', help='Additional notes')
    args = parser.parse_args()

    if args.summary:
        print_summary()
        return

    if args.log and args.file and args.type and args.before is not None and args.after is not None:
        experiment = log_experiment(
            hypothesis=args.log,
            file_changed=args.file,
            change_type=args.type,
            score_before=args.before,
            score_after=args.after,
            kept=args.kept,
            notes=args.notes
        )
        print(f"Logged experiment #{experiment['exp_id']}: {args.log}")
        print(f"  Delta: {experiment['delta']} | Kept: {experiment['kept']}")
        return

    parser.print_help()


if __name__ == '__main__':
    main()
