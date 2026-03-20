#!/usr/bin/env python3
"""
Macrocosmos SN13 Evaluation Script

Tests Macrocosmos as a potential X/Twitter data source for sentiment analysis.
EVALUATION ONLY — does not modify production code.

Usage:
    python3 utils/test_macrocosmos.py --test

Environment:
    MACROCOSMOS_API_KEY   Optional. If not set, tests unauthenticated access.
"""

import argparse
import json
import logging
import os
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("macrocosmos_eval")

BASE_URL = "https://sn13.api.macrocosmos.ai"
TIMEOUT = 15


def test_unauthenticated():
    """Test if the API works without authentication (free tier)."""
    print("\n=== Testing unauthenticated access ===")
    headers = {"Content-Type": "application/json"}
    payload = {"source": "x", "keywords": ["Kalshi"], "limit": 5}

    try:
        resp = requests.post(
            f"{BASE_URL}/api/v1/on_demand_data_request",
            headers=headers,
            json=payload,
            timeout=TIMEOUT,
        )
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text[:500]}")
        return resp
    except requests.exceptions.Timeout:
        print("TIMEOUT — endpoint unreachable")
        return None
    except Exception as e:
        print(f"ERROR: {e}")
        return None


def test_authenticated(api_key: str, query: str, source: str, limit: int) -> dict:
    """Run a single authenticated query and return metrics."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "source": source,
        "keywords": [query] if query else [],
        "limit": limit,
    }

    start = time.time()
    result = {
        "query": query,
        "source": source,
        "limit": limit,
        "response_time_sec": None,
        "status_code": None,
        "result_count": 0,
        "newest_timestamp": None,
        "oldest_timestamp": None,
        "has_text": False,
        "has_author": False,
        "has_timestamp": False,
        "has_engagement": False,
        "error": None,
        "raw_response": None,
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/api/v1/on_demand_data_request",
            headers=headers,
            json=payload,
            timeout=TIMEOUT,
        )
        elapsed = time.time() - start
        result["response_time_sec"] = round(elapsed, 2)
        result["status_code"] = resp.status_code

        # Check rate limit headers
        result["rate_limit_remaining"] = resp.headers.get("x-ratelimit-remaining", "N/A")
        result["rate_limit_limit"] = resp.headers.get("x-ratelimit-limit", "N/A")

        if resp.status_code == 200:
            data = resp.json()
            result["raw_response"] = data

            if data.get("status") == "success":
                items = data.get("data", [])
                result["result_count"] = len(items)

                if items:
                    # Check for text field
                    first = items[0]
                    result["has_text"] = any(
                        k in first
                        for k in ["text", "content", "body", "title", "submission_text"]
                    )
                    result["has_author"] = any(
                        k in first
                        for k in ["author", "username", "user", "author_name"]
                    )
                    result["has_timestamp"] = any(
                        k in first for k in ["timestamp", "created_at", "created_utc", "date"]
                    )
                    result["has_engagement"] = any(
                        k in first
                        for k in [
                            "likes",
                            "retweets",
                            "replies",
                            "upvotes",
                            "score",
                            "engagement",
                        ]
                    )

                    # Find timestamps
                    timestamps = []
                    for item in items:
                        for k in ["timestamp", "created_at", "created_utc", "date"]:
                            val = item.get(k)
                            if val:
                                timestamps.append(val)
                                break

                    if timestamps:
                        result["newest_timestamp"] = max(timestamps)
                        result["oldest_timestamp"] = min(timestamps)

            elif data.get("status") == "error":
                result["error"] = data.get("message", "unknown error")
        else:
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"

    except requests.exceptions.Timeout:
        result["error"] = "TIMEOUT"
    except Exception as e:
        result["error"] = str(e)

    return result


def run_evaluation(api_key: str = None):
    """Run all three evaluation queries."""
    api_key = api_key or os.environ.get("MACROCOSMOS_API_KEY", "")

    print("\n" + "=" * 60)
    print("MACROCOSMOS SN13 EVALUATION")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"API Key set: {'YES (len=' + str(len(api_key)) + ')' if api_key else 'NO'}")
    print("=" * 60)

    # Test 1: Unauthenticated access
    print("\n--- Unauthenticated probe ---")
    unauth_resp = test_unauthenticated()
    if unauth_resp is not None:
        print(f"Unauthenticated status: {unauth_resp.status_code}")

    # Determine what queries to run
    if not api_key:
        print("\nNo API key available — cannot run authenticated queries.")
        print("FALLBACK: Documenting service based on SDK analysis + HTTP probe.")
        save_results({
            "api_key_available": False,
            "unauthenticated_status": unauth_resp.status_code if unauth_resp else None,
            "unauthenticated_response": unauth_resp.text[:500] if unauth_resp else None,
        })
        return

    # Query 1: Kalshi prediction market
    print("\n--- Query 1: 'Kalshi prediction market' (Twitter, 100 results) ---")
    q1 = test_authenticated(api_key, "Kalshi prediction market", "x", 100)
    print_result(q1)

    # Query 2: Oil price OPEC
    print("\n--- Query 2: 'oil price OPEC' (Twitter, 100 results) ---")
    q2 = test_authenticated(api_key, "oil price OPEC", "x", 100)
    print_result(q2)

    # Query 3: Reddit wallstreetbets prediction
    print("\n--- Query 3: 'prediction' (Reddit, 50 results) ---")
    q3 = test_authenticated(api_key, "prediction", "reddit", 50)
    print_result(q3)

    results = {
        "api_key_available": True,
        "queries": [q1, q2, q3],
    }
    save_results(results)
    print_summary([q1, q2, q3])


def print_result(r: dict):
    """Print a single query result."""
    if r["error"]:
        print(f"  ERROR: {r['error']}")
        return
    print(f"  Status: {r['status_code']}")
    print(f"  Response time: {r['response_time_sec']}s")
    print(f"  Results returned: {r['result_count']}/{r['limit']}")
    print(f"  Has text field: {r['has_text']}")
    print(f"  Has author field: {r['has_author']}")
    print(f"  Has timestamp: {r['has_timestamp']}")
    print(f"  Has engagement: {r['has_engagement']}")
    if r["newest_timestamp"]:
        print(f"  Newest post: {r['newest_timestamp']}")
    if r["oldest_timestamp"]:
        print(f"  Oldest post: {r['oldest_timestamp']}")


def print_summary(results: list):
    """Print summary table."""
    print("\n" + "=" * 60)
    print("SUMMARY TABLE")
    print("=" * 60)
    print(f"{'Query':<35} {'Time':>6} {'Count':>6} {'HasText':>8} {'Freshness':>12}")
    print("-" * 60)

    for r in results:
        if r["error"]:
            freshness = f"ERROR: {r['error'][:20]}"
        elif r["result_count"] == 0:
            freshness = "NO DATA"
        else:
            freshness = f"{r['newest_timestamp'][:10] if r['newest_timestamp'] else 'N/A'}"

        print(
            f"{r['query'][:35]:<35} "
            f"{r['response_time_sec'] or 'ERR':>6} "
            f"{r['result_count']:>6} "
            f"{str(r['has_text']):>8} "
            f"{freshness:>12}"
        )

    successful = sum(1 for r in results if not r["error"] and r["result_count"] > 0)
    print(f"\nQueries with data: {successful}/{len(results)}")
    avg_time = sum(r["response_time_sec"] or 0 for r in results) / len(results)
    print(f"Average response time: {avg_time:.2f}s")


def save_results(results: dict):
    """Save raw results to logs directory."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(__file__).parent.parent / "logs" / f"macrocosmos-test-{ts}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nRaw results saved to: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Macrocosmos SN13 evaluation script")
    parser.add_argument("--test", action="store_true", help="Run evaluation")
    parser.add_argument("--api-key", type=str, default=None, help="MACROCOSMOS_API_KEY")
    args = parser.parse_args()

    if args.test or True:
        run_evaluation(api_key=args.api_key)
    else:
        parser.print_help()
