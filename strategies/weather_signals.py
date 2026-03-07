"""
Weather→Commodity Signal Generator
Maps NOAA severe weather alerts to agricultural and energy ETF signals.

Data source: NOAA Weather API (api.weather.gov) — free, no auth
Legal status: ✅ Public government data, no restrictions

Correlations:
- Drought/Heat → Corn, Wheat, Soybeans → DBA, CORN, WEAT
- Hurricane/Flood → Energy → XLE, USO
- Freeze/Frost → Citrus, Coffee → JO, NIB (less liquid)
- Severe storms (Midwest) → Grain disruption → CORN, WEAT
"""

import requests
import logging
from typing import List, Dict

# NOAA Weather API (free, no auth)
NOAA_ALERTS_URL = "https://api.weather.gov/alerts/active"
NOAA_HEADERS = {"User-Agent": "NedCarlsonBot/1.0 (trading@slimy.ai)", "Accept": "application/geo+json"}

# Weather event → ETF mapping
WEATHER_ETF_MAP = {
    # Drought/Heat in agricultural regions
    "Excessive Heat Warning": {"etfs": ["DBA", "CORN", "WEAT"], "direction": "long", "strength": 0.65},
    "Excessive Heat Watch": {"etfs": ["DBA", "CORN", "WEAT"], "direction": "long", "strength": 0.58},
    "Drought": {"etfs": ["DBA", "CORN", "WEAT"], "direction": "long", "strength": 0.70},

    # Hurricanes → Energy disruption (Gulf Coast refineries)
    "Hurricane Warning": {"etfs": ["XLE", "USO"], "direction": "long", "strength": 0.72},
    "Hurricane Watch": {"etfs": ["XLE", "USO"], "direction": "long", "strength": 0.60},
    "Tropical Storm Warning": {"etfs": ["XLE", "USO"], "direction": "long", "strength": 0.58},

    # Freeze → Crop damage
    "Freeze Warning": {"etfs": ["DBA", "WEAT"], "direction": "long", "strength": 0.62},
    "Hard Freeze Warning": {"etfs": ["DBA", "WEAT", "CORN"], "direction": "long", "strength": 0.68},
    "Frost Advisory": {"etfs": ["DBA"], "direction": "long", "strength": 0.55},

    # Severe storms in Midwest → grain transport/harvest disruption
    "Tornado Warning": {"etfs": ["CORN", "WEAT"], "direction": "long", "strength": 0.60},
    "Severe Thunderstorm Warning": {"etfs": ["CORN", "WEAT"], "direction": "long", "strength": 0.55},

    # Flooding → widespread ag damage
    "Flash Flood Warning": {"etfs": ["DBA", "CORN", "WEAT"], "direction": "long", "strength": 0.63},
    "Flood Warning": {"etfs": ["DBA", "CORN"], "direction": "long", "strength": 0.58},
}

# Agricultural states where weather matters most
AG_STATES = {"IA", "IL", "IN", "NE", "KS", "MN", "OH", "SD", "ND", "MO", "WI", "MI"}
ENERGY_STATES = {"TX", "LA", "MS", "AL", "FL"}  # Gulf Coast


def fetch_weather_signals() -> List[Dict]:
    """
    Fetch active NOAA alerts and map to trading signals.

    Returns:
        List of signal dicts with keys: etf, direction, strength, trigger, region, severity
    """
    try:
        resp = requests.get(NOAA_ALERTS_URL, headers=NOAA_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        alerts = data.get("features", [])
    except Exception as e:
        logging.warning(f"[WEATHER] NOAA API failed: {e}")
        return []

    signals = []
    seen_etfs = set()  # Avoid duplicate signals for same ETF

    for alert in alerts:
        props = alert.get("properties", {})
        event = props.get("event", "")
        severity = props.get("severity", "")
        state = props.get("areaDesc", "")

        if event not in WEATHER_ETF_MAP:
            continue
        if severity not in ("Extreme", "Severe"):
            continue

        # Check if the alert is in a relevant agricultural or energy state
        mapping = WEATHER_ETF_MAP[event]
        state_codes = [s.strip()[:2].upper() for s in state.split(",")]

        relevant_region = False
        for sc in state_codes:
            if sc in AG_STATES and any(e in ("DBA", "CORN", "WEAT") for e in mapping["etfs"]):
                relevant_region = True
            if sc in ENERGY_STATES and any(e in ("XLE", "USO") for e in mapping["etfs"]):
                relevant_region = True

        if not relevant_region:
            continue

        for etf in mapping["etfs"]:
            if etf not in seen_etfs:
                signals.append({
                    "etf": etf,
                    "direction": mapping["direction"],
                    "strength": mapping["strength"],
                    "trigger": event,
                    "region": state[:50],
                    "severity": severity,
                })
                seen_etfs.add(etf)

    for sig in signals:
        logging.info(f"[WEATHER] Signal: {sig['direction'].upper()} {sig['etf']} "
                    f"(strength={sig['strength']:.2f}, trigger={sig['trigger']}, "
                    f"region={sig['region']})")

    if not signals:
        logging.info("[WEATHER] No actionable weather signals in agricultural/energy regions")

    return signals


# Test function
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    signals = fetch_weather_signals()
    print(f"\nFound {len(signals)} weather signals:")
    for s in signals:
        print(f"  {s['direction'].upper()} {s['etf']} - {s['trigger']} ({s['strength']:.0%})")
