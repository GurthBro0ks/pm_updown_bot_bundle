#!/usr/bin/env python3
"""
Kalshi Field Smoke Test
Fetches a small number of live markets and reports field presence + normalization.
Does NOT place orders. Does NOT print secrets.
"""

import json
import os
import sys
from pathlib import Path

# Load .env before any other imports that might need it
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.kalshi import fetch_kalshi_markets


def main():
    proof_dir = os.environ.get("PROOF_DIR", "/tmp")
    out_file = os.path.join(proof_dir, "smoke_result.json")

    try:
        markets = fetch_kalshi_markets()
        if not markets:
            result = {
                "status": "OK",
                "markets_fetched": 0,
                "note": "No markets returned (normal outside market hours)",
                "fields_present": {},
                "normalized_samples": []
            }
            with open(out_file, "w") as f:
                json.dump(result, f, indent=2)
            print(f"Smoke test: 0 markets (normal outside hours)")
            print(f"Result written to: {out_file}")
            return

        # Analyze field presence on raw API responses
        # We need to look at the raw markets before normalization
        # Since fetch_kalshi_markets returns normalized, we'll do a lightweight raw fetch
        import requests
        from utils.kalshi import get_kalshi_headers, _candidate_kalshi_base_urls
        from cryptography.hazmat.primitives import serialization

        api_key = os.getenv("KALSHI_KEY")
        secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
        with open(secret_file, 'rb') as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)

        headers = get_kalshi_headers('GET', '/markets', api_key, private_key)
        raw_markets = []
        for base_url in _candidate_kalshi_base_urls():
            resp = requests.get(
                f"{base_url}/trade-api/v2/markets",
                headers=headers,
                params={'status': 'open', 'limit': 5},
                timeout=15
            )
            if resp.status_code == 200:
                raw_markets = resp.json().get('markets', [])[:5]
                break

        # Check field presence
        modern_price_fields = ['yes_bid_dollars', 'yes_ask_dollars', 'no_bid_dollars', 'no_ask_dollars']
        modern_fp_fields = ['volume_fp', 'volume_24h_fp', 'open_interest_fp']
        legacy_fields = ['yes_bid', 'yes_ask', 'volume', 'open_interest']

        fields_present = {}
        for field in modern_price_fields + modern_fp_fields + legacy_fields:
            present_count = sum(1 for m in raw_markets if field in m and m[field] is not None)
            fields_present[field] = present_count

        # Normalize first 2 markets
        from utils.kalshi_normalize import normalize_kalshi_market
        normalized_samples = []
        for m in raw_markets[:2]:
            norm = normalize_kalshi_market(m)
            # Redact any field that could be a secret
            safe = {
                "id": norm.get("id"),
                "question": norm.get("question"),
                "odds": norm.get("odds"),
                "yes_bid": norm.get("yes_bid"),
                "yes_ask": norm.get("yes_ask"),
                "volume": norm.get("volume"),
                "volume_24h": norm.get("volume_24h"),
                "open_interest": norm.get("open_interest"),
                "liquidity_usd": norm.get("liquidity_usd"),
                "hours_to_end": norm.get("hours_to_end"),
                "fees_pct": norm.get("fees_pct"),
                "raw_field_source": norm.get("raw_field_source"),
            }
            normalized_samples.append(safe)

        result = {
            "status": "OK",
            "markets_fetched": len(raw_markets),
            "fields_present": fields_present,
            "normalized_samples": normalized_samples
        }

        with open(out_file, "w") as f:
            json.dump(result, f, indent=2)

        print(f"Smoke test: {len(raw_markets)} markets fetched")
        print(f"Fields present: {fields_present}")
        print(f"Normalized sample 1: {json.dumps(normalized_samples[0], indent=2) if normalized_samples else 'N/A'}")
        print(f"Result written to: {out_file}")

    except Exception as e:
        result = {
            "status": "ERROR",
            "error": str(e)
        }
        with open(out_file, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Smoke test ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
