#!/usr/bin/env python3
"""
Kalshi RSA Authentication Connection Test

Tests that RSA-signed requests produce 200 OK responses
from the Kalshi API across multiple endpoints.

Prerequisites (env vars):
    KALSHI_KEY              - Your Kalshi API key
    KALSHI_PRIVATE_KEY_PATH - Path to your PEM-encoded RSA private key

Usage:
    python3 test_kalshi_connection.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

# Allow running from repo root or scripts/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_auth import KalshiAuth, KALSHI_BASE_URL


def test_exchange_status(auth: KalshiAuth) -> dict:
    """Test GET /trade-api/v2/exchange/status (public, but auth should still work)."""
    path = "/trade-api/v2/exchange/status"
    resp = auth.get(path)
    return {
        "endpoint": path,
        "method": "GET",
        "status_code": resp.status_code,
        "ok": resp.status_code == 200,
        "body": resp.json() if resp.status_code == 200 else resp.text[:200],
    }


def test_get_events(auth: KalshiAuth) -> dict:
    """Test GET /trade-api/v2/events (requires auth)."""
    path = "/trade-api/v2/events"
    resp = auth.get(path, params={"limit": 3, "status": "open"})
    return {
        "endpoint": path,
        "method": "GET",
        "status_code": resp.status_code,
        "ok": resp.status_code == 200,
        "events_count": len(resp.json().get("events", [])) if resp.status_code == 200 else 0,
    }


def test_get_balance(auth: KalshiAuth) -> dict:
    """Test GET /trade-api/v2/portfolio/balance (requires auth)."""
    path = "/trade-api/v2/portfolio/balance"
    resp = auth.get(path)
    return {
        "endpoint": path,
        "method": "GET",
        "status_code": resp.status_code,
        "ok": resp.status_code == 200,
        "body": resp.json() if resp.status_code == 200 else resp.text[:200],
    }


def test_get_positions(auth: KalshiAuth) -> dict:
    """Test GET /trade-api/v2/portfolio/positions (requires auth)."""
    path = "/trade-api/v2/portfolio/positions"
    resp = auth.get(path)
    return {
        "endpoint": path,
        "method": "GET",
        "status_code": resp.status_code,
        "ok": resp.status_code == 200,
        "body": resp.json() if resp.status_code == 200 else resp.text[:200],
    }


def test_order_format(auth: KalshiAuth) -> dict:
    """
    Verify the order-placement request is correctly formatted.
    Does NOT actually submit an order -- only builds and validates the payload.
    """
    path = "/trade-api/v2/portfolio/orders"
    headers = auth.get_headers("POST", path)

    payload = {
        "action": "buy",
        "type": "limit",
        "side": "yes",
        "count": 1,
        "yes_price": 1,         # $0.01 -- minimum penny trade
        "ticker": "DRY-RUN-TICKER",
    }

    # Validate header keys
    required_keys = {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE", "KALSHI-ACCESS-TIMESTAMP"}
    present_keys = set(headers.keys()) & required_keys
    has_all = present_keys == required_keys

    # Validate signature looks like base64
    sig = headers.get("KALSHI-ACCESS-SIGNATURE", "")
    sig_valid = len(sig) > 40 and "==" not in sig[:10]

    return {
        "endpoint": path,
        "method": "POST (DRY-RUN -- not sent)",
        "headers_present": sorted(present_keys),
        "all_headers_ok": has_all,
        "signature_length": len(sig),
        "payload": payload,
        "ok": has_all and sig_valid,
    }


def main():
    print("=" * 60)
    print("  Kalshi RSA Authentication Connection Test")
    print("=" * 60)
    print()

    # Check env vars
    api_key = os.environ.get("KALSHI_KEY", "")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")

    if not api_key or not key_path:
        print("SKIP: KALSHI_KEY and/or KALSHI_PRIVATE_KEY_PATH not set.")
        print("Set these environment variables to run live authentication tests.")
        print()
        print("Running dry-run header validation only...")
        print()

        # Even without creds we can verify the module loads and api_type is correct
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import runner
        api_type = runner.VENUE_CONFIGS["kalshi"]["api_type"]
        print(f"  runner.py  kalshi.api_type = {api_type}")
        if api_type == "rest_rsa_signed":
            print("  PASS: api_type is rest_rsa_signed (not Basic auth)")
        else:
            print(f"  FAIL: expected 'rest_rsa_signed', got '{api_type}'")
            sys.exit(1)

        print()
        print("=" * 60)
        print("  DRY-RUN COMPLETE (set env vars for live tests)")
        print("=" * 60)
        sys.exit(0)

    # Live tests
    try:
        auth = KalshiAuth(api_key=api_key, private_key_path=key_path)
    except Exception as e:
        print(f"FAIL: Could not initialise KalshiAuth: {e}")
        sys.exit(1)

    tests = [
        ("Exchange Status", test_exchange_status),
        ("Get Events", test_get_events),
        ("Portfolio Balance", test_get_balance),
        ("Portfolio Positions", test_get_positions),
        ("Order Format (dry-run)", test_order_format),
    ]

    results = []
    all_ok = True
    for name, fn in tests:
        print(f"Testing: {name} ...")
        try:
            result = fn(auth)
            results.append(result)
            status = "PASS" if result["ok"] else "FAIL"
            code = result.get("status_code", "n/a")
            print(f"  {status}  status={code}")
            if not result["ok"]:
                all_ok = False
                print(f"  Detail: {json.dumps(result, indent=4)}")
        except Exception as e:
            all_ok = False
            print(f"  ERROR: {e}")
            results.append({"endpoint": name, "ok": False, "error": str(e)})
        print()

    # Summary
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed

    print("=" * 60)
    print(f"  Results: {passed}/{len(results)} passed, {failed} failed")
    print("=" * 60)

    # Save proof
    proof = {
        "test": "kalshi_rsa_auth_connection",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "auth_method": "RSA-PKCS1v15-SHA256",
        "base_url": KALSHI_BASE_URL,
        "results": results,
        "all_ok": all_ok,
    }
    proof_path = f"/tmp/proof_kalshi_auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(proof_path, "w") as f:
        json.dump(proof, f, indent=2)
    print(f"Proof saved: {proof_path}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
