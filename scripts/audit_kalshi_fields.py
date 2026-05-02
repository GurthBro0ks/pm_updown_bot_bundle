#!/usr/bin/env python3
"""
Kalshi Deprecated Field Audit Script
Walks all .py files and reports usage of old vs new Kalshi API fields.
"""

import os
import re
import sys
from pathlib import Path

OLD_FIELDS = [
    "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price",
    "previous_yes_bid", "previous_yes_ask", "previous_price",
    "volume", "volume_24h", "open_interest", "liquidity", "risk_limit_cents"
]

NEW_FIELDS = [
    "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars",
    "last_price_dollars", "volume_fp", "volume_24h_fp", "open_interest_fp",
    "yes_bid_size_fp", "yes_ask_size_fp", "notional_value_dollars"
]

EXCLUDE_DIRS = {'.git', 'venv', '.venv', '__pycache__', 'node_modules', '.removed_20260227'}
EXCLUDE_PATTERNS = [
    r'/proofs/',
    r'/backups?/',
    r'\.removed_',
]


def should_exclude(path: str) -> bool:
    parts = path.split(os.sep)
    if any(ex in parts for ex in EXCLUDE_DIRS):
        return True
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, path):
            return True
    return False


def classify_hit(filepath: str, line: str, field: str) -> str:
    """Heuristic classification of a field hit."""
    line_lower = line.lower()
    filepath_lower = filepath.lower()

    # Test/mock fixtures
    if 'test' in filepath_lower or 'mock' in filepath_lower:
        return "MOCK"

    # Comments/docstrings
    stripped = line.strip()
    if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
        return "COMMENT"

    # Normalizer/internal layer
    if 'normalize' in filepath_lower or 'kalshi_normalize' in filepath_lower:
        return "INTERNAL"

    # API response parsing
    if any(kw in line_lower for kw in ['.get(', '[]', 'response', 'api', 'fetch', 'json(', 'market']):
        return "CRITICAL"

    # Strategy logic using internal names
    if 'strategies' in filepath_lower:
        return "STRATEGY"

    # Default
    return "CRITICAL"


def find_hits():
    hits = []
    repo_root = Path('/opt/slimy/pm_updown_bot_bundle')

    for pyfile in repo_root.rglob('*.py'):
        filepath = str(pyfile)
        if should_exclude(filepath):
            continue

        try:
            with open(pyfile, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"SKIP {filepath}: {e}")
            continue

        for lineno, line in enumerate(lines, 1):
            # Check old fields (but not if part of new field name)
            for field in OLD_FIELDS:
                # Word boundary regex that excludes being a prefix/suffix of new fields
                pattern = rf'\b{re.escape(field)}\b'
                if re.search(pattern, line):
                    # Additional check: make sure it's not part of a new field name
                    # e.g. "yes_bid" should not match inside "yes_bid_dollars"
                    # The word boundary handles most, but let's be extra safe
                    for new_field in NEW_FIELDS:
                        if field in new_field and new_field in line:
                            break
                    else:
                        classification = classify_hit(filepath, line, field)
                        hits.append({
                            'file': filepath,
                            'line': lineno,
                            'content': line.rstrip('\n'),
                            'field': field,
                            'type': 'OLD',
                            'classification': classification
                        })

            # Check new fields
            for field in NEW_FIELDS:
                pattern = rf'\b{re.escape(field)}\b'
                if re.search(pattern, line):
                    classification = classify_hit(filepath, line, field)
                    hits.append({
                        'file': filepath,
                        'line': lineno,
                        'content': line.rstrip('\n'),
                        'field': field,
                        'type': 'NEW',
                        'classification': classification
                    })

    return hits


def main():
    hits = find_hits()

    # Group by classification
    by_class = {}
    for h in hits:
        by_class.setdefault(h['classification'], []).append(h)

    out_path = os.environ.get('PROOF_DIR', '/tmp')
    out_file = os.path.join(out_path, 'deprecated_field_audit.txt')

    with open(out_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("KALSHI DEPRECATED FIELD AUDIT\n")
        f.write(f"Total hits: {len(hits)}\n")
        f.write("=" * 80 + "\n\n")

        # Summary counts
        old_count = sum(1 for h in hits if h['type'] == 'OLD')
        new_count = sum(1 for h in hits if h['type'] == 'NEW')
        f.write(f"OLD field hits: {old_count}\n")
        f.write(f"NEW field hits: {new_count}\n\n")

        for classification in ['CRITICAL', 'INTERNAL', 'MOCK', 'STRATEGY', 'COMMENT']:
            if classification not in by_class:
                continue
            f.write(f"\n{'=' * 80}\n")
            f.write(f"CLASSIFICATION: {classification} ({len(by_class[classification])} hits)\n")
            f.write(f"{'=' * 80}\n")
            for h in by_class[classification]:
                f.write(f"\n{h['file']}:{h['line']}\n")
                f.write(f"  FIELD: {h['field']} ({h['type']})\n")
                f.write(f"  LINE:  {h['content']}\n")

    print(f"Audit written to: {out_file}")
    print(f"Total hits: {len(hits)} (OLD: {old_count}, NEW: {new_count})")


if __name__ == '__main__':
    main()
