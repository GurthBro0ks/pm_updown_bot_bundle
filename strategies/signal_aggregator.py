#!/usr/bin/env python3
"""
Signal Aggregator — unified view of every signal source in the Bayesian cascade.

Single entry point: get_all_signals() returns a unified dict of all sources.

API endpoints:
  GET /api/signals/current  — latest signal state (60s cache)
  GET /api/signals/history  — time-series over last N hours

Sources:
  - gdelt:    strategies.gdelt_signal
  - grok:     data/sentiment_cache/ (most recent entry by provider)
  - glm:      data/sentiment_cache/ (most recent entry by provider)
  - stock_hunter: proofs/phase3_stock_hunter_*.json (most recent)
  - kelly:    config.py + data/circuit_breaker.json

CLI: python3 strategies/signal_aggregator.py --test
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure BASE_DIR is on path for strategy imports
BASE_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("signal_aggregator")

PROOFS_DIR = BASE_DIR / "proofs"
CACHE_DIR = BASE_DIR / "data" / "sentiment_cache"
GDELT_CACHE = Path("/tmp/gdelt_signal_cache.json")
CIRCUIT_BREAKER_FILE = BASE_DIR / "data" / "circuit_breaker.json"
PAPER_BALANCE = BASE_DIR / "paper_trading" / "paper_balance.json"

# Staleness thresholds (seconds)
STALE_GDELT = 3600       # 1 hour
STALE_PROOF = 7200       # 2 hours
STALE_SENTIMENT = 7200   # 2 hours

# Cache for get_all_signals (60-second TTL)
_current_cache: Optional[dict] = None
_current_cache_at: float = 0.0
CACHE_TTL = 60.0


# ─── Helpers ────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except Exception:
        return None


def _is_stale(path: Path, threshold: float) -> bool:
    """Return True if file mtime is older than threshold seconds."""
    m = _mtime(path)
    if m is None:
        return True
    return (time.time() - m) > threshold


def _iso_from_mtime(mtime: float) -> str:
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return dt.isoformat()


# ─── Source collectors ─────────────────────────────────────────

def _collect_gdelt() -> dict:
    """Collect GDELT geopolitical signal."""
    try:
        from strategies.gdelt_signal import get_gdelt_signal
        signal = get_gdelt_signal()
        stale = _is_stale(GDELT_CACHE, STALE_GDELT)
        return {
            "status": "ok" if not stale else "stale",
            "geo_risk_score": signal.get("geo_risk_score", 0.5),
            "event_count": signal.get("event_count", 0),
            "avg_tone": signal.get("avg_tone", 0.5),
            "top_events": signal.get("top_events", [])[:5],
            "regions": signal.get("regions", {}),
            "last_updated": signal.get("timestamp"),
            "cached": signal.get("cached", False),
        }
    except Exception as e:
        logger.warning(f"[signals] GDELT collection failed: {e}")
        return {
            "status": "error",
            "geo_risk_score": None,
            "event_count": 0,
            "avg_tone": None,
            "top_events": [],
            "regions": {},
            "last_updated": None,
            "cached": False,
        }


def _most_recent_sentiment_by_provider(provider: str) -> dict:
    """Find most recent sentiment cache entry for a given provider."""
    if not CACHE_DIR.exists():
        return {}

    candidates = []
    for f in CACHE_DIR.glob("*.json"):
        data = _read_json(f)
        if data.get("provider") == provider:
            candidates.append((f.stat().st_mtime, data))

    if not candidates:
        return {}

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, data = candidates[0]
    return data


def _collect_grok() -> dict:
    """Collect Grok AI provider status."""
    entry = _most_recent_sentiment_by_provider("grok_420") or \
            _most_recent_sentiment_by_provider("grok_fast")

    if not entry:
        return {
            "status": "disabled",
            "model": None,
            "last_sentiment": None,
            "last_market": None,
            "last_updated": None,
        }

    stale = True
    last_updated = None
    for f in CACHE_DIR.glob("*.json"):
        data = _read_json(f)
        if data.get("provider") in ("grok_420", "grok_fast"):
            m = _mtime(f)
            if m and (not stale or (time.time() - m) < STALE_SENTIMENT):
                stale = False
                last_updated = _iso_from_mtime(m)
            elif last_updated is None:
                last_updated = _iso_from_mtime(m)

    model = entry.get("provider", "grok_420") if entry else None
    return {
        "status": "ok" if not stale else "stale",
        "model": model,
        "last_sentiment": entry.get("probability") if entry else None,
        "last_market": entry.get("market_id") if entry else None,
        "last_updated": last_updated,
    }


def _collect_glm() -> dict:
    """Collect GLM AI provider status."""
    entry = _most_recent_sentiment_by_provider("glm")

    if not entry:
        return {
            "status": "disabled",
            "model": None,
            "last_sentiment": None,
            "last_market": None,
            "last_updated": None,
        }

    stale = True
    last_updated = None
    for f in CACHE_DIR.glob("*.json"):
        data = _read_json(f)
        if data.get("provider") == "glm":
            m = _mtime(f)
            if m and (time.time() - m) < STALE_SENTIMENT:
                stale = False
                last_updated = _iso_from_mtime(m)
            elif last_updated is None:
                last_updated = _iso_from_mtime(m)

    return {
        "status": "ok" if not stale else "stale",
        "model": "glm",
        "last_sentiment": entry.get("probability") if entry else None,
        "last_market": entry.get("market_id") if entry else None,
        "last_updated": last_updated,
    }


def _collect_stock_hunter() -> dict:
    """Collect latest stock hunter proof data."""
    try:
        proofs = sorted(
            PROOFS_DIR.glob("phase3_stock_hunter_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not proofs:
            return {
                "status": "error",
                "active_tickers": 0,
                "last_scan_time": None,
                "top_signals": [],
                "news_sources_active": [],
            }

        latest = _read_json(proofs[0])
        stale = _is_stale(proofs[0], STALE_PROOF)

        results = latest.get("data", {}).get("results", [])
        orders = latest.get("data", {}).get("orders", [])

        # Top signals: top 10 by combined_sentiment
        all_signals = sorted(
            [(r["ticker"], r.get("combined_sentiment", 0), r) for r in results],
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        top_signals = []
        for ticker, score, r in all_signals:
            # Determine primary news source
            source = "unknown"
            if r.get("alpha_vantage_sentiment") is not None:
                source = "alpha_vantage"
            elif r.get("news_count", 0) > 0:
                source = "finnhub"

            top_signals.append({
                "ticker": ticker,
                "sentiment": round(score, 4),
                "source": source,
                "passes_threshold": r.get("passes_threshold", False),
            })

        # Active news sources
        apis = latest.get("apis", {})
        news_sources_active = [
            k for k, v in apis.items() if v and k in ("finnhub", "alpha_vantage")
        ]

        return {
            "status": "ok" if not stale else "stale",
            "active_tickers": len(results),
            "last_scan_time": results[0].get("timestamp") if results else None,
            "top_signals": top_signals,
            "news_sources_active": news_sources_active,
            "positions_open": len(orders),
        }
    except Exception as e:
        logger.warning(f"[signals] Stock Hunter collection failed: {e}")
        return {
            "status": "error",
            "active_tickers": 0,
            "last_scan_time": None,
            "top_signals": [],
            "news_sources_active": [],
        }


def _collect_kelly() -> dict:
    """Collect Kelly pipeline status."""
    try:
        # Read circuit breaker for current bankroll and drawdown
        cb = _read_json(CIRCUIT_BREAKER_FILE)
        balance = _read_json(PAPER_BALANCE)

        # Read config for edge threshold
        try:
            import sys
            sys.path.insert(0, str(BASE_DIR))
            from config import RISK_CAPS, KELLY_FRAC_SHADOW
            edge_threshold = RISK_CAPS.get("edge_after_fees_pct", 1.5) / 100.0
            kelly_fraction = KELLY_FRAC_SHADOW
        except Exception:
            edge_threshold = 0.015
            kelly_fraction = 0.25

        # Count positions sized from last proof
        proofs = sorted(PROOFS_DIR.glob("phase3_stock_hunter_*.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        positions_sized = 0
        if proofs:
            latest = _read_json(proofs[0])
            orders = latest.get("data", {}).get("orders", [])
            positions_sized = len(orders)

        # Last kelly calculation time
        last_calc = None
        if cb:
            # Use timestamp from circuit breaker or balance file
            for f in [CIRCUIT_BREAKER_FILE, PAPER_BALANCE]:
                m = _mtime(f)
                if m:
                    last_calc = _iso_from_mtime(m)
                    break

        return {
            "status": "ok",
            "current_fraction": kelly_fraction,
            "edge_threshold": edge_threshold,
            "positions_sized": positions_sized,
            "last_calculated": last_calc,
            "bankroll": balance.get("cash", 100.0),
            "peak_bankroll": cb.get("peak_bankroll") if cb else None,
            "current_drawdown_pct": cb.get("current_drawdown_pct", 0.0) if cb else 0.0,
            "halt_triggered": cb.get("halt_triggered", False) if cb else False,
        }
    except Exception as e:
        logger.warning(f"[signals] Kelly collection failed: {e}")
        return {
            "status": "error",
            "current_fraction": None,
            "edge_threshold": None,
            "positions_sized": 0,
            "last_calculated": None,
        }


def _cascade_summary(sources: dict) -> dict:
    """Compute cascade health summary."""
    total = len(sources)
    healthy = sum(1 for s in sources.values() if s.get("status") == "ok")
    degraded = sum(1 for s in sources.values() if s.get("status") == "stale")
    down = sum(1 for s in sources.values() if s.get("status") in ("error", "disabled"))

    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from config import SENTIMENT_PROVIDERS
        cascade_order = SENTIMENT_PROVIDERS
    except Exception:
        cascade_order = ["grok_420", "grok_fast", "glm"]

    return {
        "total_sources": total,
        "sources_healthy": healthy,
        "sources_degraded": degraded,
        "sources_down": down,
        "cascade_order": cascade_order,
    }


# ─── Main API ─────────────────────────────────────────────────

def get_all_signals(force_refresh: bool = False) -> dict:
    """
    Collect and return the current state of all signal sources.

    Cached for 60 seconds to avoid repeated expensive collections.
    Set force_refresh=True to bypass cache.
    """
    global _current_cache, _current_cache_at

    now = time.time()
    if not force_refresh and _current_cache and (now - _current_cache_at) < CACHE_TTL:
        return _current_cache

    t0 = now

    sources = {
        "gdelt": _collect_gdelt(),
        "grok": _collect_grok(),
        "glm": _collect_glm(),
        "stock_hunter": _collect_stock_hunter(),
        "kelly": _collect_kelly(),
    }

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "collection_time_ms": round((time.time() - t0) * 1000, 1),
        "sources": sources,
        "cascade_summary": _cascade_summary(sources),
    }

    _current_cache = result
    _current_cache_at = now

    elapsed = (time.time() - t0) * 1000
    logger.info(f"[signals] Aggregated {len(sources)} sources in {elapsed:.1f}ms")

    return result


def get_signal_history(hours: int = 24, interval_minutes: int = 30) -> list:
    """
    Build a time-series of signal values from phase3_stock_hunter proof files.

    Groups proofs by `interval_minutes` time buckets.
    Each entry has: timestamp, gdelt_risk, stock_sentiment, top_ticker.

    Returns list of snapshots.
    """
    cutoff = time.time() - (hours * 3600)
    interval_secs = interval_minutes * 60

    # Find phase3_stock_hunter proofs within the window
    proofs = []
    for f in sorted(PROOFS_DIR.glob("phase3_stock_hunter_*.json"), reverse=True):
        m = _mtime(f)
        if m and m >= cutoff:
            proofs.append((m, f))

    if not proofs:
        return []

    # Group by time bucket — one entry per bucket
    buckets: dict[int, tuple] = {}
    for m, f in proofs:
        bucket = int(m // interval_secs)
        # Keep the most recent file per bucket
        if bucket not in buckets or m > buckets[bucket][0]:
            buckets[bucket] = (m, f)

    history = []
    for bucket, (m, f) in sorted(buckets.items(), reverse=True):
        ts = datetime.fromtimestamp(m, tz=timezone.utc).isoformat()
        data = _read_json(f)
        results = data.get("data", {}).get("results", [])

        entry: dict = {
            "timestamp": ts,
            "proof_file": f.name,
        }

        # GDELT risk from live cache
        gdelt = _collect_gdelt()
        entry["gdelt_risk"] = gdelt.get("geo_risk_score")

        # Top stock signal
        if results:
            top = max(results, key=lambda r: r.get("combined_sentiment", 0))
            entry["stock_sentiment"] = top.get("combined_sentiment")
            entry["top_ticker"] = top.get("ticker")
            # Also include top 3 signals
            top3 = sorted(results, key=lambda r: r.get("combined_sentiment", 0), reverse=True)[:3]
            entry["top_signals"] = [
                {"ticker": r["ticker"], "sentiment": r.get("combined_sentiment")}
                for r in top3
            ]
        else:
            entry["stock_sentiment"] = None
            entry["top_ticker"] = None
            entry["top_signals"] = []

        history.append(entry)

    return history


# ─── CLI ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Signal Aggregator")
    parser.add_argument("--test", action="store_true", help="Pretty-print all signals")
    parser.add_argument("--history", action="store_true", help="Print signal history")
    parser.add_argument("--hours", type=int, default=24, help="Hours of history")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )

    if args.history:
        print(f"=== Signal History (last {args.hours}h) ===")
        history = get_signal_history(hours=args.hours)
        for h in history[:20]:
            print(f"  {h['timestamp'][:19]}  "
                  f"gdelt={h.get('gdelt_risk', 'N/A')}  "
                  f"stock={h.get('stock_sentiment', 'N/A')}  "
                  f"top={h.get('top_ticker', 'N/A')}")
        print(f"\n{len(history)} history entries")
        sys.exit(0)

    print("=== SIGNAL AGGREGATOR TEST ===")
    print()

    signals = get_all_signals(force_refresh=True)

    # Color helpers
    def status_color(s):
        return {"ok": "✓", "stale": "⚠", "error": "✗", "disabled": "⊘"}.get(s, "?")

    def color_score(val, low=0.3, high=0.7):
        if val is None:
            return f"\033[90mN/A\033[0m"
        if val >= high:
            return f"\033[92m{val:.4f}\033[0m"
        if val <= low:
            return f"\033[91m{val:.4f}\033[0m"
        return f"\033[93m{val:.4f}\033[0m"

    summary = signals["cascade_summary"]
    print(f"CASCADE STATUS: {summary['sources_healthy']}/{summary['total_sources']} healthy  "
          f"({summary['sources_degraded']} degraded, {summary['sources_down']} down)")
    print(f"Collection time: {signals['collection_time_ms']}ms")
    print(f"Updated: {signals['timestamp'][:19]}")
    print()

    # GDELT
    g = signals["sources"]["gdelt"]
    print(f"  {'GDELT':<12} [{status_color(g['status'])}]  "
          f"risk={color_score(g['geo_risk_score'])}  "
          f"events={g['event_count']}  "
          f"tone={g.get('avg_tone', 'N/A')}")
    if g.get("top_events"):
        for e in g["top_events"][:2]:
            print(f"             • {e.get('title', '')[:60]}")

    # Grok
    k = signals["sources"]["grok"]
    print(f"  {'GROK':<12} [{status_color(k['status'])}]  "
          f"model={k.get('model', 'N/A')}  "
          f"last_sentiment={color_score(k.get('last_sentiment'))}  "
          f"market={k.get('last_market', 'N/A')}")

    # GLM
    m = signals["sources"]["glm"]
    print(f"  {'GLM':<12} [{status_color(m['status'])}]  "
          f"last_sentiment={color_score(m.get('last_sentiment'))}  "
          f"market={m.get('last_market', 'N/A')}")

    # Stock Hunter
    s = signals["sources"]["stock_hunter"]
    print(f"  {'STOCK_HUNTER':<12} [{status_color(s['status'])}]  "
          f"active_tickers={s['active_tickers']}  "
          f"positions_open={s.get('positions_open', 0)}  "
          f"sources={s.get('news_sources_active', [])}")
    for sig in s.get("top_signals", [])[:5]:
        print(f"             {sig['ticker']:<6} "
              f"sentiment={color_score(sig.get('sentiment'))}  "
              f"source={sig.get('source', 'unknown')}")

    # Kelly
    y = signals["sources"]["kelly"]
    print(f"  {'KELLY':<12} [{status_color(y['status'])}]  "
          f"fraction={y.get('current_fraction', 'N/A')}  "
          f"edge={y.get('edge_threshold', 'N/A')}  "
          f"sized={y.get('positions_sized', 0)}")

    print()
    print("=== Cascade Order ===")
    for i, src in enumerate(summary["cascade_order"], 1):
        print(f"  {i}. {src}")

    print()
    if summary["sources_down"] > 0:
        print("⚠ WARNING: Some sources are down or disabled")
        sys.exit(1)
    elif summary["sources_degraded"] > 0:
        print("⚠ NOTICE: Some sources are stale but functional")
    else:
        print("✓ All sources healthy")
