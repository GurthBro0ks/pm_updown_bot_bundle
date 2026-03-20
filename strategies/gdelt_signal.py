#!/usr/bin/env python3
"""
GDELT Geopolitical Signal Integration

Fetches real-time geopolitical risk from GDELT (Global Database of Events,
Language, and Tone) — free, no API key required, updates every 15 minutes.

Data sources:
  - GDELT v2 Events API: conflict/military/sanctions events
  - GDELT GKG Tone API: global average tone as fear/risk indicator

Geopolitical risk score (0.0–1.0):
  - 0.0–0.3: Calm period → boost "no" on conflict markets
  - 0.3–0.7: Neutral → no adjustment
  - 0.7–1.0: High risk → boost "yes" on conflict/escalation markets

Cache: /tmp/gdelt_signal_cache.json (30-minute TTL)
CLI: python3 strategies/gdelt_signal.py --test
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─── Logging ────────────────────────────────────────────────────
logger = logging.getLogger("gdelt_signal")

# ─── GDELT API endpoints (free, no auth) ───────────────────────
GDELT_EVENTS_URL = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=conflict%20OR%20sanctions%20OR%20military%20OR%20war%20OR%20missile"
    "%20OR%20nuclear%20OR%20invasion%20OR%20attack&mode=artlist&maxrecords=50&format=json"
)

GDELT_TONE_URL = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=*&mode=tonechart&format=json"
)

# ─── Cache ──────────────────────────────────────────────────────
CACHE_FILE = Path("/tmp/gdelt_signal_cache.json")
CACHE_TTL_SECONDS = 1800  # 30 minutes


def _read_cache() -> Optional[dict]:
    """Read cached signal if fresh."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data.get("_cached_at", 0) < CACHE_TTL_SECONDS:
            data["cached"] = True
            return data
    except Exception:
        pass
    return None


def _write_cache(data: dict):
    """Write signal to cache."""
    try:
        data["_cached_at"] = time.time()
        CACHE_FILE.write_text(json.dumps(data))
    except Exception as e:
        logger.warning(f"[GDELT] Cache write failed: {e}")


# ─── Keyword multipliers ────────────────────────────────────────
HIGH_RISK_KEYWORDS = {
    "war": 1.4,
    "invasion": 1.5,
    "nuclear": 1.5,
    "missile": 1.3,
    "sanctions": 1.2,
    "attack": 1.3,
    "military": 1.1,
    "conflict": 1.1,
    "escalation": 1.4,
    "ceasefire": 0.7,
    "peace": 0.6,
    "treaty": 0.7,
    "diplomacy": 0.7,
}

# ─── Region → Kalshi market tag mapping ────────────────────────
REGION_TAGS = {
    "middle east": "geopolitical_mideast",
    "ukraine": "geopolitical_ukraine",
    "russia": "geopolitical_russia",
    "china": "geopolitical_china",
    "taiwan": "geopolitical_taiwan",
    "korea": "geopolitical_korea",
    "europe": "geopolitical_europe",
    "africa": "geopolitical_africa",
    "latin america": "geopolitical_latam",
    "arctic": "geopolitical_arctic",
}


def _parse_tone(tone_data: dict) -> float:
    """Extract average tone score from GKG tone chart response."""
    try:
        # Tone chart returns an array of tone buckets
        tones = tone_data.get("tones", [])
        if not tones:
            return 0.0
        # Weighted average tone (negative = riskier)
        total_weight = sum(t.get("weight", 1) for t in tones)
        weighted_tone = sum(t.get("tone", 0) * t.get("weight", 1) for t in tones)
        avg_tone = weighted_tone / total_weight if total_weight else 0.0
        # Tone is typically -5 to +5; normalize to 0–1 (0 = very negative/fearful, 1 = very positive)
        normalized = (avg_tone + 5) / 10
        return max(0.0, min(1.0, normalized))
    except Exception:
        return 0.5  # neutral on parse failure


def _parse_events(events_data: dict) -> tuple[list[dict], float, int]:
    """
    Parse GDELT event list into top events, avg tone, and count.

    Returns:
        (top_events, avg_tone, event_count)
    """
    articles = events_data.get("articles", []) or []

    event_count = len(articles)
    if event_count == 0:
        return [], 0.5, 0

    # Extract top events by tone (most negative = highest risk)
    parsed = []
    for art in articles:
        try:
            title = art.get("title", "")
            url = art.get("url", "")
            domain = art.get("domain", "")
            # GDELT sometimes provides seendate or published
            seendate = art.get("seendate", art.get("published", ""))
            # Social magnitude as rough event significance
            social_magnitude = float(art.get("socialimage", art.get("shares", "0")) or 0)

            # Compute keyword risk multiplier
            text = title.lower()
            kw_multiplier = 1.0
            for kw, mult in HIGH_RISK_KEYWORDS.items():
                if kw in text:
                    kw_multiplier = max(kw_multiplier, mult)

            # Detect region
            detected_regions = []
            for region, tag in REGION_TAGS.items():
                if region in text:
                    detected_regions.append(tag)

            parsed.append({
                "title": title[:200],
                "url": url,
                "domain": domain,
                "seendate": seendate,
                "tone": art.get("tone", 0.0),
                "kw_multiplier": kw_multiplier,
                "regions": detected_regions,
                "magnitude": social_magnitude,
            })
        except Exception:
            continue

    # Sort by keyword multiplier (riskiest first)
    parsed.sort(key=lambda x: x["kw_multiplier"], reverse=True)

    # Compute average tone from parsed events
    tones = [p["tone"] for p in parsed if p["tone"] is not None]
    avg_tone = sum(tones) / len(tones) if tones else 0.0

    # Normalize tone: GDELT tone is typically -5 to +5
    # -5 = very negative/fearful → high risk
    # +5 = very positive → low risk
    # Invert so higher = riskier
    avg_tone_normalized = avg_tone  # already in -5..5 range from GDELT
    # Convert to 0..1 risk: 0 = calm, 1 = fearful
    # tone=-5 → risk=1.0, tone=0 → risk=0.5, tone=+5 → risk=0.0
    tone_risk = max(0.0, min(1.0, 0.5 - (avg_tone_normalized / 10)))

    return parsed[:5], tone_risk, event_count


def _compute_geo_risk(
    event_count: int,
    tone_risk: float,
    top_events: list[dict],
) -> float:
    """
    Compute composite geopolitical risk score 0.0–1.0.

    Components:
      - Event count (normalized, capped at 50 events = max 0.3 contribution)
      - Tone risk (0.0–1.0, contribution 0.0–0.4)
      - Keyword multiplier from top event (0.0–0.3)
    """
    # Event count contribution (log scale, max 50 events = full 0.3)
    import math
    count_contribution = min(0.3, 0.3 * math.log1p(event_count) / math.log1p(50))

    # Tone contribution (0–0.4, inverted: negative tone = high risk)
    tone_contribution = tone_risk * 0.4

    # Keyword multiplier from top event (0–0.3)
    kw_contribution = 0.0
    if top_events:
        top_mult = top_events[0].get("kw_multiplier", 1.0)
        # Scale: 1.0 → 0.0, 1.5 → 0.3
        kw_contribution = max(0.0, (top_mult - 1.0) * 0.6)

    raw_score = count_contribution + tone_contribution + kw_contribution
    return max(0.0, min(1.0, raw_score))


def fetch_gdelt_signal() -> dict:
    """
    Fetch and compute GDELT geopolitical risk signal.

    Returns:
        {
            "geo_risk_score": float,       # 0.0–1.0
            "event_count": int,
            "avg_tone": float,              # raw GDELT tone (-5 to +5)
            "tone_risk": float,             # normalized 0.0–1.0
            "top_events": list[dict],
            "regions": dict,                # tag → count
            "cached": bool,
            "timestamp": str,
        }
    """
    # Check cache first
    cached = _read_cache()
    if cached:
        logger.info(
            f"[GDELT] Cache hit: risk={cached.get('geo_risk_score', 0):.2f}, "
            f"events={cached.get('event_count', 0)}, cached=True"
        )
        return cached

    logger.info("[GDELT] Fetching fresh signal from GDELT API...")

    # Fetch events list (with retry on 429)
    events_data = {}
    events_ok = False
    for attempt in range(3):
        try:
            resp = requests.get(GDELT_EVENTS_URL, timeout=10)
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                logger.warning(f"[GDELT] Events API rate-limited, retrying in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            events_data = resp.json()
            events_ok = True
            break
        except Exception as e:
            logger.warning(f"[GDELT] Events API failed (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(5)
    if not events_ok:
        logger.warning("[GDELT] Events API failed after 3 attempts, using fallback")

    # GDELT requires ≥5 seconds between requests — sleep 15s before tone API
    time.sleep(15)

    # Fetch tone chart (with retry on 429)
    tone_data = {}
    tone_ok = False
    for attempt in range(3):
        try:
            resp = requests.get(GDELT_TONE_URL, timeout=10)
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                logger.warning(f"[GDELT] Tone API rate-limited, retrying in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            tone_data = resp.json()
            tone_ok = True
            break
        except Exception as e:
            logger.warning(f"[GDELT] Tone API failed (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(5)
    if not tone_ok:
        logger.warning("[GDELT] Tone API failed after 3 attempts, using fallback")

    # Parse events
    top_events, tone_risk, event_count = _parse_events(events_data)

    # Parse tone
    tone_score = _parse_tone(tone_data)
    if tone_score != 0.5:  # Only use GKG tone if we got a real value
        tone_risk = 1.0 - tone_score  # invert: high tone = calm

    # Compute composite risk
    geo_risk_score = _compute_geo_risk(event_count, tone_risk, top_events)

    # Aggregate regions
    regions: dict = {}
    for evt in top_events:
        for tag in evt.get("regions", []):
            regions[tag] = regions.get(tag, 0) + 1

    signal = {
        "geo_risk_score": round(geo_risk_score, 4),
        "event_count": event_count,
        "avg_tone": round(tone_risk, 4),
        "tone_risk": round(tone_risk, 4),
        "top_events": top_events,
        "regions": regions,
        "cached": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Log summary
    logger.info(
        f"[GDELT] risk={geo_risk_score:.2f}, events={event_count}, "
        f"tone={tone_risk:.2f}, regions={list(regions.keys())}"
    )
    for i, evt in enumerate(signal["top_events"][:5]):
        logger.info(
            f"  [{i+1}] {evt.get('title', '')[:80]} "
            f"(tone={evt.get('tone', 0):.2f}, mult={evt.get('kw_multiplier', 1.0):.2f})"
        )

    # Cache result
    _write_cache(signal)

    return signal


def get_gdelt_signal() -> dict:
    """
    Public API — returns current GDELT geopolitical risk signal.
    Uses cache if fresh, fetches if stale or missing.
    """
    return fetch_gdelt_signal()


def apply_gdelt_prior(
    base_prob: float,
    market_title: str,
    market_category: str = "",
    geo_signal: dict = None,
) -> float:
    """
    Apply GDELT geopolitical risk as a Bayesian prior adjustment.

    Args:
        base_prob: Base probability from other signals (0.0–1.0)
        market_title: Market title for keyword matching
        market_category: Market category (e.g., "Politics", "Economics")
        geo_signal: Pre-fetched GDELT signal dict. If None, fetches fresh.

    Returns:
        Adjusted probability (clamped 0.01–0.99)
    """
    if geo_signal is None:
        geo_signal = get_gdelt_signal()

    risk = geo_signal.get("geo_risk_score", 0.5)
    text = (market_title + " " + market_category).lower()

    # Check if this market is geopolitical
    is_geopolitical = any(
        tag.replace("geopolitical_", "") in text
        for tag in [
            "mideast", "ukraine", "russia", "china", "taiwan",
            "korea", "europe", "africa", "latam", "arctic",
            "conflict", "war", "military", "sanctions", "nuclear",
        ]
    )

    if not is_geopolitical:
        # No geopolitical relevance — no adjustment
        return base_prob

    # Determine adjustment direction
    if risk > 0.7:
        # High risk period — boost "yes" on conflict/escalation markets
        adjustment = (risk - 0.7) * 0.15  # max +7.5%
        adjusted = base_prob + adjustment
        logger.info(
            f"[kelly] GDELT prior: risk={risk:.2f}, events="
            f"{geo_signal.get('event_count', 0)}, tone={geo_signal.get('avg_tone', 0):.2f}, "
            f"boost=+{adjustment:.4f} → {adjusted:.4f}"
        )
    elif risk < 0.3:
        # Calm period — boost "no" on conflict markets
        adjustment = (0.3 - risk) * 0.10  # max +3%
        adjusted = base_prob - adjustment
        logger.info(
            f"[kelly] GDELT prior: risk={risk:.2f}, events="
            f"{geo_signal.get('event_count', 0)}, tone={geo_signal.get('avg_tone', 0):.2f}, "
            f"boost=-{adjustment:.4f} → {adjusted:.4f}"
        )
    else:
        # Neutral (0.3–0.7) — no adjustment
        adjusted = base_prob
        logger.info(
            f"[kelly] GDELT prior: risk={risk:.2f}, events="
            f"{geo_signal.get('event_count', 0)}, tone={geo_signal.get('avg_tone', 0):.2f}, "
            f"neutral (0.3–0.7 band)"
        )

    return max(0.01, min(0.99, adjusted))


# ─── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    parser = argparse.ArgumentParser(description="GDELT Geopolitical Signal")
    parser.add_argument("--test", action="store_true", help="Run in test mode and print signal")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )

    if args.test:
        print("=" * 60)
        print("GDELT GEOPOLITICAL SIGNAL TEST")
        print("=" * 60)

        signal = get_gdelt_signal()

        print(f"\ngeo_risk_score : {signal['geo_risk_score']:.4f}")
        print(f"event_count    : {signal['event_count']}")
        print(f"avg_tone       : {signal['avg_tone']:.4f}")
        print(f"tone_risk      : {signal['tone_risk']:.4f}")
        print(f"cached         : {signal['cached']}")
        print(f"timestamp      : {signal['timestamp']}")
        print(f"regions        : {signal['regions']}")

        print(f"\nTop {len(signal['top_events'])} events:")
        for i, evt in enumerate(signal["top_events"][:5], 1):
            print(f"  [{i}] {evt['title'][:80]}")
            print(f"       tone={evt.get('tone', 0):.2f}, mult={evt.get('kw_multiplier', 1.0):.2f}, "
                  f"regions={evt.get('regions', [])}")

        # Test prior application
        print(f"\n{'='*60}")
        print("GDELT PRIOR APPLICATION TESTS")
        print("=" * 60)

        test_cases = [
            ("Will Russia invade Ukraine in 2026?", "Politics"),
            ("Will Iran develop nuclear weapons?", "Politics"),
            ("Will there be a Middle East war?", "Politics"),
            ("Will the Fed cut rates?", "Economics"),
            ("Will AAPL beat earnings?", "Companies"),
        ]

        for title, cat in test_cases:
            base = 0.5
            adj = apply_gdelt_prior(base, title, cat, signal)
            diff = adj - base
            print(f"\n  Base={base:.2f} → Adjusted={adj:.4f} ({diff:+.4f})")
            print(f"  Market: {title[:60]}")

        print(f"\n{'='*60}")
        # Validate
        if 0.0 <= signal["geo_risk_score"] <= 1.0:
            print("✓ PASS: geo_risk_score is between 0.0 and 1.0")
        else:
            print("✗ FAIL: geo_risk_score out of range")
            sys.exit(1)

        if CACHE_FILE.exists():
            print(f"✓ PASS: Cache file exists at {CACHE_FILE}")
        else:
            print("✗ INFO: Cache file not yet written (first run)")

        sys.exit(0)
