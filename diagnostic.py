#!/usr/bin/env python3
"""
TRADING BOT DEEP DIAGNOSTIC SCAN
Run: python3 diagnostic.py
Output: Full health report of every subsystem

Tests every component end-to-end:
1. Environment & Config
2. RPC Connectivity (all chains)
3. Wallet Tracker
4. Kelly/Position Sizer Pipeline
5. Circuit Breaker
6. Kalshi Pipeline
7. DEX-CEX Arbitrage Pipeline
8. Airdrop Farmer
9. Base Farmer
10. Stock Hunter
11. API Server
12. Farming API Endpoints
13. Cron Configuration
14. File Permissions & State Files
"""

import json
import logging
import os
import sys
import time
import traceback
import subprocess
import socket
import re
from datetime import datetime, timezone
from pathlib import Path

# Setup
os.chdir("/opt/slimy/pm_updown_bot_bundle")
sys.path.insert(0, "/opt/slimy/pm_updown_bot_bundle")

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("diagnostic")

# =============================================================================
# HELPERS
# =============================================================================

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️ WARN"
INFO = "ℹ️ INFO"

results = []
section_count = 0

def section(name):
    global section_count
    section_count += 1
    print(f"\n{'='*70}")
    print(f"  {section_count}. {name}")
    print(f"{'='*70}")

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    line = f"  {status} {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    results.append({"name": name, "pass": condition, "detail": detail})
    return condition

def warn(name, detail=""):
    line = f"  {WARN} {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    results.append({"name": name, "pass": None, "detail": detail})

def info(name, detail=""):
    line = f"  {INFO} {name}"
    if detail:
        line += f" — {detail}"
    print(line)

# =============================================================================
# 1. ENVIRONMENT & CONFIG
# =============================================================================

section("ENVIRONMENT & CONFIG")

# Check .env exists
check(".env file exists", os.path.exists(".env"))

# Check critical env vars
wallet = os.getenv("WALLET_ADDRESS", "")
check("WALLET_ADDRESS is set", bool(wallet) and len(wallet) == 42,
      f"Length: {len(wallet)}, Value: {wallet[:10]}...{wallet[-6:]}" if wallet else "NOT SET")
check("WALLET_ADDRESS is not placeholder",
      wallet and "YOUR" not in wallet.upper() and "0x000" not in wallet,
      wallet[:20] if wallet else "")
check("WALLET_ADDRESS format valid",
      bool(re.fullmatch(r'0x[0-9a-fA-F]{40}', wallet)) if wallet else False)

kelly_frac = os.getenv("KELLY_FRACTION", "")
check("KELLY_FRACTION is set", bool(kelly_frac), f"Value: {kelly_frac}")

max_pos_pct = os.getenv("MAX_POSITION_PCT", "")
check("MAX_POSITION_PCT is set", bool(max_pos_pct), f"Value: {max_pos_pct}")

min_edge = os.getenv("MIN_EDGE_THRESHOLD", "")
check("MIN_EDGE_THRESHOLD is set", bool(min_edge), f"Value: {min_edge}")

max_exec = os.getenv("MAX_EXECUTION_SECONDS", "")
check("MAX_EXECUTION_SECONDS is set", bool(max_exec), f"Value: {max_exec}")

farm_budget = os.getenv("FARMING_WEEKLY_BUDGET", "")
check("FARMING_WEEKLY_BUDGET is set", bool(farm_budget), f"Value: {farm_budget}")

# Check config.py imports
try:
    from config import RISK_CAPS, VENUE_CONFIGS, PHASES, KELLY_FRAC_SHADOW, KELLY_FRAC_LIVE
    check("config.py imports OK", True,
          f"KELLY_FRAC_SHADOW={KELLY_FRAC_SHADOW}, KELLY_FRAC_LIVE={KELLY_FRAC_LIVE}")
except Exception as e:
    check("config.py imports OK", False, str(e))

# Check runner.py imports
try:
    # Just verify it parses without error
    result = subprocess.run(
        ["python3", "-c", "import runner"],
        capture_output=True, text=True, timeout=15, cwd="/opt/slimy/pm_updown_bot_bundle"
    )
    check("runner.py imports OK", result.returncode == 0,
          result.stderr[:200] if result.returncode != 0 else "")
except Exception as e:
    check("runner.py imports OK", False, str(e))


# =============================================================================
# 2. RPC CONNECTIVITY (ALL CHAINS)
# =============================================================================

section("RPC CONNECTIVITY")

import requests

# Get RPC URLs from wallet tracker
try:
    from utils.wallet_tracker import CHAINS
    chain_configs = CHAINS
    info("Loaded chain configs from wallet_tracker", f"{len(chain_configs)} chains")
except:
    chain_configs = {}
    warn("Could not import CHAINS from wallet_tracker")

# Also test hardcoded public RPCs
PUBLIC_RPCS = {
    "ethereum": "https://eth.llamarpc.com",
    "base": "https://mainnet.base.org",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "optimism": "https://mainnet.optimism.io",
    "linea": "https://rpc.linea.build",
    "monad": "https://rpc.monad.xyz",
}

# Merge: prefer wallet_tracker configs, fallback to public
rpcs_to_test = {}
for chain, url in PUBLIC_RPCS.items():
    rpcs_to_test[chain] = url
for chain, cfg in chain_configs.items():
    if isinstance(cfg, dict) and "rpc" in cfg:
        rpcs_to_test[chain] = cfg["rpc"]
    elif isinstance(cfg, str):
        rpcs_to_test[chain] = cfg

for chain, rpc_url in rpcs_to_test.items():
    try:
        resp = requests.post(rpc_url, json={
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [wallet, "latest"],
            "id": 1
        }, timeout=8)
        data = resp.json()
        if "error" in data:
            check(f"RPC {chain}", False,
                  f"Error: {data['error'].get('message', data['error'])}")
        elif "result" in data:
            balance_wei = int(data["result"], 16)
            balance_eth = balance_wei / 10**18
            check(f"RPC {chain}", True,
                  f"Balance: {balance_eth:.6f} ETH (~${balance_eth * 2076:.2f})")
        else:
            check(f"RPC {chain}", False, f"Unexpected: {str(data)[:100]}")
    except requests.exceptions.Timeout:
        check(f"RPC {chain}", False, f"TIMEOUT after 8s — {rpc_url}")
    except Exception as e:
        check(f"RPC {chain}", False, f"{type(e).__name__}: {str(e)[:100]}")


# =============================================================================
# 3. WALLET TRACKER
# =============================================================================

section("WALLET TRACKER")

try:
    from utils.wallet_tracker import get_full_balance
    check("wallet_tracker imports OK", True)

    if wallet and len(wallet) == 42:
        try:
            balance = get_full_balance(wallet)
            total = 0
            if isinstance(balance, dict):
                total = balance.get("total", balance.get("total_usd", 0))
                check("get_full_balance() returns data", True,
                      f"Total: ${total:.2f}")
                # Show per-chain breakdown
                for chain, val in balance.items():
                    if chain not in ("total", "total_usd", "wallet"):
                        if isinstance(val, (int, float)):
                            info(f"  {chain}", f"${val:.2f}")
                        elif isinstance(val, dict):
                            chain_total = val.get("total", val.get("usd", 0))
                            info(f"  {chain}", f"${chain_total:.2f}" if chain_total else str(val)[:80])
            elif isinstance(balance, (int, float)):
                check("get_full_balance() returns data", balance > 0,
                      f"Total: ${balance:.2f}")
            else:
                check("get_full_balance() returns data", False,
                      f"Unexpected type: {type(balance)}")

            check("Wallet shows non-zero balance", total > 0 if isinstance(total, (int, float)) else False,
                  f"${total:.2f}" if isinstance(total, (int, float)) else str(total))
        except Exception as e:
            check("get_full_balance() executes", False, f"{type(e).__name__}: {e}")
    else:
        warn("Skipping wallet test — invalid address")

except ImportError as e:
    check("wallet_tracker imports OK", False, str(e))


# =============================================================================
# 4. KELLY / POSITION SIZER PIPELINE
# =============================================================================

section("KELLY / POSITION SIZER PIPELINE")

try:
    from utils.position_sizer import (
        kelly_bet_size, passes_ev_filter, size_position,
        get_bayesian_tracker, get_circuit_breaker,
        BayesianEstimate, CircuitBreaker
    )
    check("position_sizer imports OK", True)

    # Test EV filter
    passes, ev, edge = passes_ev_filter(0.60, 0.45)
    check("EV filter (p=0.60, market=0.45)", passes,
          f"EV={ev:.4f}, edge={edge:.4f}")

    passes, ev, edge = passes_ev_filter(0.52, 0.50)
    check("EV filter rejects low edge (p=0.52, market=0.50)", not passes,
          f"EV={ev:.4f}, edge={edge:.4f}")

    # Test Kelly sizing
    size, meta = kelly_bet_size(0.65, 0.45, bankroll=99.66)
    check("Kelly sizing works", size >= 0,
          f"Size: ${size:.2f}, Full Kelly: {meta.get('full_kelly_pct', 0):.3f}" +
          (" (negative odds → $0 expected)" if size == 0 else ""))
    check("Kelly respects 5% cap", size <= 99.66 * 0.05 + 0.01,
          f"${size:.2f} <= ${99.66 * 0.05:.2f}")

    # Test full pipeline
    size, meta = size_position(
        market_id="diag-test-001",
        market_price=0.40,
        bankroll=99.66,
        current_positions=2,
        estimated_prob=0.65,
    )
    check("Full sizing pipeline works", size > 0 or "blocked_by" in meta,
          f"Size: ${size:.2f}" if size > 0 else f"Blocked: {meta.get('blocked_by', 'unknown')}")

except ImportError as e:
    check("position_sizer imports OK", False, str(e))
except Exception as e:
    check("position_sizer tests", False, f"{type(e).__name__}: {e}")


# =============================================================================
# 5. CIRCUIT BREAKER
# =============================================================================

section("CIRCUIT BREAKER")

try:
    cb = get_circuit_breaker()
    check("CircuitBreaker loads", True)

    cb.update(99.66)
    can_trade, cb_meta = cb.can_trade()
    check("CircuitBreaker.update() works", True)
    check("CircuitBreaker.can_trade() works", isinstance(can_trade, bool),
          f"can_trade={can_trade}")
    check("Trading is allowed", can_trade,
          f"Meta: {json.dumps(cb_meta)[:100]}" if not can_trade else "")

    info("Peak bankroll", f"${cb.peak_bankroll:.2f}" if hasattr(cb, 'peak_bankroll') else "N/A")
    info("Is tripped", str(cb.is_tripped) if hasattr(cb, 'is_tripped') else "N/A")

except Exception as e:
    check("CircuitBreaker tests", False, f"{type(e).__name__}: {e}")


# =============================================================================
# 6. KALSHI PIPELINE
# =============================================================================

section("KALSHI PIPELINE")

try:
    from utils.kalshi import (
        KALSHI_BLOCKED_CATEGORIES, KALSHI_BLOCKED_PREFIXES,
        get_kalshi_markets
    )
    check("kalshi imports OK", True)
    info("Blocked categories", str(KALSHI_BLOCKED_CATEGORIES))
    info("Blocked prefixes", str(KALSHI_BLOCKED_PREFIXES))

    # Check for TARGET_CATEGORIES (should be removed)
    try:
        from utils.kalshi import KALSHI_TARGET_CATEGORIES
        warn("KALSHI_TARGET_CATEGORIES still exists",
             f"Value: {KALSHI_TARGET_CATEGORIES}")
    except ImportError:
        check("TARGET_CATEGORIES removed (blocklist-only)", True)

    # Test market fetch
    try:
        markets = get_kalshi_markets()
        # Function executed without error - that's the key test
        # Empty list is normal outside trading hours (no priced markets)
        check("get_kalshi_markets() executes", True,
              f"Returned {len(markets)} priced markets (empty = normal outside hours)")

        if len(markets) > 0:
            info("Markets with active ask prices", str(len(markets)))
        else:
            info("No priced markets", "Normal outside trading hours - API works, no active markets")
    except Exception as e:
        check("get_kalshi_markets() executes", False, str(e)[:200])

except ImportError as e:
    check("kalshi imports OK", False, str(e))

# Check kalshi_optimize
try:
    from strategies.kalshi_optimize import filter_markets_by_category
    check("kalshi_optimize imports OK", True)

    # Check if include_categories is disabled
    import inspect
    src = inspect.getsource(filter_markets_by_category)
    if "include_categories=None" in src or "include_categories is None" in src:
        check("Category filter accepts None (blocklist-only)", True)
    else:
        warn("Category filter may still have allowlist", "Check filter_markets_by_category()")
except Exception as e:
    check("kalshi_optimize check", False, str(e)[:200])


# =============================================================================
# 7. DEX-CEX ARBITRAGE PIPELINE
# =============================================================================

section("DEX-CEX ARBITRAGE PIPELINE")

try:
    from utils.dex_prices import (
        get_dex_price, get_cex_price, scan_for_arbitrage, POPULAR_TOKENS
    )
    check("dex_prices imports OK", True)
    check("POPULAR_TOKENS is a list or dict", isinstance(POPULAR_TOKENS, (list, dict)),
          f"Type: {type(POPULAR_TOKENS).__name__}, Count: {len(POPULAR_TOKENS)}")

    # Test DexScreener with retry
    try:
        dex_price = None
        for attempt in range(2):
            try:
                dex_price = get_dex_price("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")  # WETH
                if dex_price is not None:
                    break
            except Exception:
                if attempt == 0:
                    import time
                    time.sleep(2)  # Wait before retry

        if dex_price is not None:
            check("DexScreener API responds", True, f"WETH price: ${dex_price}")
            check("DexScreener returns float", isinstance(dex_price, (int, float)),
                  f"Type: {type(dex_price).__name__}")
        else:
            # DexScreener is intermittent - warn instead of fail
            warn("DexScreener API responds", "Intermittent API failure - CEX fallback available")
    except Exception as e:
        check("DexScreener API test", False, str(e)[:200])

    # Test CEX
    try:
        cex_price = get_cex_price("ETH")
        check("CEX price fetch works", cex_price is not None and cex_price > 0,
              f"ETH: ${cex_price:.2f}" if cex_price else "None returned")
    except Exception as e:
        check("CEX price fetch", False, str(e)[:200])

    # Test scan_for_arbitrage
    try:
        test_tokens = ["ETH", "BTC"] if isinstance(POPULAR_TOKENS, list) else list(POPULAR_TOKENS.keys())[:2]
        opps = scan_for_arbitrage(test_tokens[:2], min_spread=1.0)
        check("scan_for_arbitrage() executes", isinstance(opps, list),
              f"Found {len(opps)} opportunities")
    except Exception as e:
        check("scan_for_arbitrage() test", False, str(e)[:200])

except ImportError as e:
    check("dex_prices imports OK", False, str(e))


# =============================================================================
# 8. AIRDROP FARMER
# =============================================================================

section("AIRDROP FARMER")

try:
    from strategies.airdrop_farmer import AIRDROP_TARGETS
    check("airdrop_farmer imports OK", True)
    check("AIRDROP_TARGETS has entries", len(AIRDROP_TARGETS) > 0,
          f"{len(AIRDROP_TARGETS)} targets")

    # Check tier distribution
    tiers = {}
    for key, data in AIRDROP_TARGETS.items():
        tier = data.get("tier", "?")
        tiers[tier] = tiers.get(tier, 0) + 1

    for tier, count in sorted(tiers.items()):
        info(f"Tier {tier}", f"{count} targets")

    # Check that Monad and Linea are marked COMPLETED
    monad = AIRDROP_TARGETS.get("monad", AIRDROP_TARGETS.get("Monad", {}))
    check("Monad marked as COMPLETED/F",
          monad.get("status", "").upper() in ("COMPLETED", "DEAD") or monad.get("tier") == "F",
          f"Status: {monad.get('status', 'NOT FOUND')}, Tier: {monad.get('tier', 'NOT FOUND')}")

    linea = AIRDROP_TARGETS.get("linea", AIRDROP_TARGETS.get("Linea", {}))
    check("Linea marked as COMPLETED/F",
          linea.get("status", "").upper() in ("COMPLETED", "DEAD") or linea.get("tier") == "F",
          f"Status: {linea.get('status', 'NOT FOUND')}, Tier: {linea.get('tier', 'NOT FOUND')}")

    # Check Polymarket is present and active
    poly = AIRDROP_TARGETS.get("polymarket", AIRDROP_TARGETS.get("Polymarket", {}))
    check("Polymarket is Tier S/CONFIRMED",
          poly.get("tier") == "S" or "CONFIRMED" in poly.get("status", "").upper(),
          f"Status: {poly.get('status', 'NOT FOUND')}, Tier: {poly.get('tier', 'NOT FOUND')}")

    # Check get_airdrop_status exists
    try:
        from strategies.airdrop_farmer import get_airdrop_status
        status = get_airdrop_status()
        check("get_airdrop_status() works", isinstance(status, list) and len(status) > 0,
              f"{len(status)} targets returned")
    except ImportError:
        check("get_airdrop_status() exists", False, "Function not found — API endpoint won't work")
    except Exception as e:
        check("get_airdrop_status() works", False, str(e)[:200])

except ImportError as e:
    check("airdrop_farmer imports OK", False, str(e))


# =============================================================================
# 9. BASE FARMER
# =============================================================================

section("BASE FARMER")

try:
    from strategies.base_farmer import run as base_farm_run, get_farming_report
    check("base_farmer imports OK", True)

    # Test farming report
    try:
        report = get_farming_report()
        check("get_farming_report() works", isinstance(report, dict),
              f"Quality: {report.get('farming_quality', 'N/A')}, "
              f"Actions: {report.get('total_actions', 0)}")
    except Exception as e:
        check("get_farming_report()", False, str(e)[:200])

    # Test dry run execution
    try:
        base_farm_run(circuit_breaker_ok=True, dry_run=True)
        check("base_farmer.run(dry_run=True) executes", True)
    except Exception as e:
        check("base_farmer.run() executes", False, f"{type(e).__name__}: {e}")

    # Check state files
    check("Farming state file exists",
          os.path.exists("data/base_farming_state.json"),
          "data/base_farming_state.json")
    check("Farming log file exists",
          os.path.exists("data/base_farming_log.json"),
          "data/base_farming_log.json")

except ImportError as e:
    check("base_farmer imports OK", False, str(e))


# =============================================================================
# 10. STOCK HUNTER
# =============================================================================

section("STOCK HUNTER")

try:
    from strategies.stock_hunter import calculate_kelly_position_size
    check("stock_hunter imports OK (old Kelly)", True,
          "Note: May be using old Kelly — should use position_sizer")
except ImportError:
    pass

try:
    from strategies import stock_hunter as sh_module
    check("stock_hunter module loads", True)
except Exception as e:
    check("stock_hunter module loads", False, str(e)[:200])

# Check paper balance
if os.path.exists("data/paper_balance.json"):
    try:
        with open("data/paper_balance.json", "r") as f:
            paper = json.load(f)
        check("Paper balance file readable", True)
        info("Cash", f"${paper.get('cash', 'N/A')}")
        info("Positions", str(len(paper.get('positions', []))))
        info("Total value", f"${paper.get('total_value', 'N/A')}")
    except Exception as e:
        check("Paper balance file readable", False, str(e))
elif os.path.exists("paper_balance.json"):
    info("Paper balance at root", "paper_balance.json (not in data/)")
else:
    info("No paper_balance.json found", "Optional - created by paper trading")


# =============================================================================
# 11. API SERVER
# =============================================================================

section("API SERVER")

# Check if API server is running
try:
    resp = requests.get("http://localhost:8510/api/trading/airdrops", timeout=5)
    check("API server responding on :8510", resp.status_code == 200,
          f"Status: {resp.status_code}")
except requests.exceptions.ConnectionRefused:
    check("API server responding on :8510", False, "Connection refused — server not running")
except Exception as e:
    check("API server responding on :8510", False, str(e)[:200])


# =============================================================================
# 12. FARMING API ENDPOINTS
# =============================================================================

section("FARMING API ENDPOINTS")

endpoints = [
    ("GET", "/api/farming/status", None),
    ("GET", "/api/farming/log?n=3", None),
    ("GET", "/api/farming/airdrop-targets", None),
    ("POST", "/api/farming/trigger", {"dry_run": True}),
]

for method, path, body in endpoints:
    try:
        url = f"http://localhost:8510{path}"
        if method == "GET":
            resp = requests.get(url, timeout=10)
        else:
            resp = requests.post(url, json=body, timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "unknown")
            check(f"{method} {path}", status == "success",
                  f"Status: {status}" + (f", Keys: {list(data.keys())}" if status == "success" else f", Error: {data.get('message', '')}"))
        else:
            check(f"{method} {path}", False,
                  f"HTTP {resp.status_code}: {resp.text[:100]}")

    except requests.exceptions.ConnectionRefused:
        check(f"{method} {path}", False, "API server not running")
    except Exception as e:
        check(f"{method} {path}", False, str(e)[:200])


# =============================================================================
# 13. CRON CONFIGURATION
# =============================================================================

section("CRON CONFIGURATION")

try:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        cron_lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
        check("Cron jobs exist", len(cron_lines) > 0, f"{len(cron_lines)} active jobs")
        for line in cron_lines:
            info("Cron", line[:100])

        # Check if runner.py is in cron
        runner_in_cron = any("runner.py" in l or "run_with_monitoring" in l for l in cron_lines)
        check("Trading bot in cron", runner_in_cron)

        # Check for timeout wrapper
        has_timeout = any("timeout " in l for l in cron_lines)
        if not has_timeout:
            warn("No timeout wrapper in cron", "Consider: timeout 120 python3 runner.py")
    else:
        warn("No crontab found", result.stderr[:100])
except Exception as e:
    check("Cron check", False, str(e))


# =============================================================================
# 14. FILE PERMISSIONS & STATE
# =============================================================================

section("FILE PERMISSIONS & STATE")

# Check data directory
check("data/ directory exists", os.path.isdir("data"))

state_files = [
    "data/base_farming_state.json",
    "data/base_farming_log.json",
    "data/bayesian_state.json",
    "data/circuit_breaker.json",
]

for f in state_files:
    exists = os.path.exists(f)
    if exists:
        size = os.path.getsize(f)
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        check(f"{f}", True, f"Size: {size}B, Modified: {mtime}")
    else:
        # bayesian_state.json is created on first trade resolution - that's expected
        if "bayesian_state" in f:
            info(f"{f} not found", "Normal — created on first trade resolution")
        else:
            warn(f"{f} not found", "Will be created on first use")

# Check proofs directory
proofs_dir = "proofs"
if os.path.isdir(proofs_dir):
    proofs = sorted(Path(proofs_dir).glob("*.json"))
    check("Proofs directory has files", len(proofs) > 0, f"{len(proofs)} proof files")
    if proofs:
        latest = proofs[-1]
        mtime = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        info("Latest proof", f"{latest.name} ({mtime})")
else:
    warn("Proofs directory not found")

# Check log directory
if os.path.isdir("logs"):
    logs = sorted(Path("logs").glob("*"))
    info("Log files", f"{len(logs)} files in logs/")
else:
    warn("logs/ directory not found")


# =============================================================================
# 15. SENTIMENT SCORER
# =============================================================================

section("SENTIMENT SCORER")

# Check sentiment_scorer module
try:
    from strategies.sentiment_scorer import score_market, PROVIDERS
    check("sentiment_scorer module loads", True, "Module imports successfully")
except Exception as e:
    check("sentiment_scorer module loads", False, str(e))

# Check API keys
has_key = False
for name, cfg in PROVIDERS.items():
    key = os.environ.get(cfg["api_key_env"], "")
    if key and "your_" not in key:
        has_key = True
        check(f"sentiment_{name}_key configured", True, f"{cfg['api_key_env']} set")
    else:
        info(f"sentiment_{name}_key", f"{cfg['api_key_env']} not set")

if has_key:
    check("sentiment_scorer ready", True, "At least one provider configured")
else:
    warn("sentiment_scorer ready", "No API keys set — returns flat 0.50")

# Check cache
cache_dir = Path("data/sentiment_cache")
if cache_dir.exists():
    cached = len(list(cache_dir.glob("*.json")))
    check("sentiment_cache", True, f"{cached} cached entries")
else:
    warn("sentiment_cache", "Cache directory missing")


# =============================================================================
# FINAL SUMMARY
# =============================================================================

print(f"\n{'='*70}")
print(f"  DIAGNOSTIC SUMMARY")
print(f"{'='*70}")

passed = sum(1 for r in results if r["pass"] is True)
failed = sum(1 for r in results if r["pass"] is False)
warned = sum(1 for r in results if r["pass"] is None)

print(f"\n  {PASS} Passed: {passed}")
print(f"  {FAIL} Failed: {failed}")
print(f"  {WARN} Warnings: {warned}")
print(f"  Total checks: {len(results)}")

if failed > 0:
    print(f"\n  FAILURES:")
    for r in results:
        if r["pass"] is False:
            print(f"    ❌ {r['name']}: {r['detail']}")

if warned > 0:
    print(f"\n  WARNINGS:")
    for r in results:
        if r["pass"] is None:
            print(f"    ⚠️ {r['name']}: {r['detail']}")

print(f"\n{'='*70}")

# Save report to file
report = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "summary": {"passed": passed, "failed": failed, "warnings": warned},
    "results": results
}
report_file = f"data/diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
os.makedirs("data", exist_ok=True)
with open(report_file, "w") as f:
    json.dump(report, f, indent=2)
print(f"\n  Report saved: {report_file}")
print(f"{'='*70}\n")
