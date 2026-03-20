#!/usr/bin/env python3
"""
Create Hold-Out Test Set
Scans existing proof files by date, reserves most recent 60 days as hold-out.
The autoresearch loop must NEVER score against holdout during optimization.
Only used for validation checkpoints every 25 experiments.

Usage:
    python3 scripts/create_holdout.py
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def extract_date_from_filename(filename: str) -> datetime:
    """Extract date from proof filename like shipping_mode_all_20260320_110029.json."""
    match = re.search(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', filename)
    if match:
        year, month, day, hour, minute, second = map(int, match.groups())
        return datetime(year, month, day, hour, minute, second)
    return datetime.min


def scan_proof_files(proof_dir: str) -> list:
    """Scan proofs directory and return files with their timestamps."""
    proof_path = Path(proof_dir)
    if not proof_path.exists():
        return []

    files = []
    for f in proof_path.glob('*.json'):
        ts = extract_date_from_filename(f.name)
        if ts != datetime.min:
            files.append({
                'path': str(f),
                'name': f.name,
                'timestamp': ts,
                'date': ts.date(),
            })
    return sorted(files, key=lambda x: x['timestamp'], reverse=True)


def create_holdout_set(proof_dir: str, holdout_dir: str, days: int = 60) -> dict:
    """
    Create holdout set from most recent proof files.
    Returns manifest with date range and file count.
    """
    proof_path = Path(proof_dir)
    holdout_path = Path(holdout_dir)

    # Clear existing holdout
    if holdout_path.exists():
        shutil.rmtree(holdout_path)
    holdout_path.mkdir(parents=True, exist_ok=True)

    # Scan all proof files
    files = scan_proof_files(proof_dir)
    if not files:
        return {
            'status': 'no_files',
            'message': 'No dated proof files found',
            'holdout_dir': str(holdout_path),
            'days_requested': days,
            'files_copied': 0,
        }

    # Determine cutoff date (most recent file minus days)
    most_recent = files[0]['timestamp']
    cutoff = most_recent - timedelta(days=days)

    # Split into holdout (recent) and training (older)
    holdout_files = [f for f in files if f['timestamp'] > cutoff]
    training_files = [f for f in files if f['timestamp'] <= cutoff]

    # Copy holdout files
    copied = []
    for f in holdout_files:
        dest = holdout_path / f['name']
        shutil.copy2(f['path'], dest)
        copied.append(f['name'])

    # Determine actual date range
    if holdout_files:
        date_range = {
            'oldest': min(f['timestamp'] for f in holdout_files).isoformat(),
            'newest': max(f['timestamp'] for f in holdout_files).isoformat(),
        }
    else:
        date_range = {'oldest': None, 'newest': None}

    # Create manifest
    manifest = {
        'created': datetime.now().isoformat(),
        'holdout_dir': str(holdout_path),
        'days_requested': days,
        'days_available': len(set(f['date'] for f in holdout_files)),
        'cutoff_date': cutoff.isoformat(),
        'most_recent_file_date': most_recent.isoformat(),
        'date_range': date_range,
        'files_copied': len(copied),
        'file_count_by_type': _count_by_type(copied),
        'training_files_count': len(training_files),
        'note': f'Reduced window - only {len(holdout_files)} files in {days}-day range' if len(holdout_files) < days * 2 else None,
    }

    # Write manifest
    manifest_path = holdout_path / 'MANIFEST.json'
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    # Also copy pnl.db if it exists
    db_src = os.path.join(os.path.dirname(proof_dir.rstrip('/')), 'paper_trading', 'pnl.db')
    if os.path.exists(db_src):
        db_dest = holdout_path.parent / 'holdout_pnl.db'
        shutil.copy2(db_src, db_dest)
        manifest['pnl_db_copied'] = str(db_dest)

    return manifest


def _count_by_type(filenames: list) -> dict:
    """Count files by type prefix."""
    counts = {}
    for name in filenames:
        for prefix in ['kalshi_optimized', 'phase3_stock_hunter', 'sef_spot', 'crypto_spot', 'shipping_mode', 'gdelt', 'airdrop_farming']:
            if name.startswith(prefix):
                counts[prefix] = counts.get(prefix, 0) + 1
                break
        else:
            counts['other'] = counts.get('other', 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description='Create holdout test set from proof files')
    parser.add_argument('--proof-dir', default='proofs/', help='Path to proofs directory')
    parser.add_argument('--holdout-dir', default='data/holdout/', help='Path for holdout directory')
    parser.add_argument('--days', type=int, default=60, help='Number of days to reserve as holdout')
    args = parser.parse_args()

    # Resolve paths relative to bot root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    proof_dir = args.proof_dir
    holdout_dir = args.holdout_dir

    if not os.path.isabs(proof_dir):
        proof_dir = os.path.join(script_dir, '..', proof_dir)
    if not os.path.isabs(holdout_dir):
        holdout_dir = os.path.join(script_dir, '..', holdout_dir)

    if not os.path.exists(proof_dir):
        print(f"ERROR: Proof directory not found: {proof_dir}")
        return

    print(f"Scanning proof files in: {proof_dir}")
    manifest = create_holdout_set(proof_dir, holdout_dir, days=args.days)

    print()
    print("=" * 50)
    print("HOLD-OUT SET CREATED")
    print("=" * 50)
    print(f"  Holdout Directory: {manifest['holdout_dir']}")
    print(f"  Days Requested:     {manifest['days_requested']}")
    print(f"  Days Available:     {manifest['days_available']}")
    print(f"  Files Copied:       {manifest['files_copied']}")
    print(f"  Training Files:     {manifest['training_files_count']}")
    print("-" * 50)
    print(f"  Cutoff Date:        {manifest['cutoff_date']}")
    if manifest.get('note'):
        print(f"  Note:               {manifest['note']}")
    print("-" * 50)
    if manifest['date_range']['oldest']:
        print(f"  Date Range (oldest): {manifest['date_range']['oldest']}")
        print(f"  Date Range (newest): {manifest['date_range']['newest']}")
    print("=" * 50)
    print(f"\nManifest written to: {manifest['holdout_dir']}/MANIFEST.json")


if __name__ == '__main__':
    main()
