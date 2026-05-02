"""
Open-Meteo GFS Ensemble Weather Forecast Module

Fetches 31-member GFS ensemble forecasts from Open-Meteo (FREE, no API key needed).
Extracts daily HIGH temperature from each ensemble member for target date.

Kalshi KXHIGH markets are about the DAILY HIGH temperature, not instantaneous temps.
We extract max(hourly_temp) for each member across the relevant day in local time.

City configs (coordinates + timezone):
  NYC (Central Park):  40.7829, -73.9654, America/New_York
  Chicago (O'Hare):    41.9742, -87.9073, America/Chicago
  Miami:               25.7617, -80.1918, America/New_York
  LA:                  34.0522, -118.2437, America/Los_Angeles
  Denver:              39.7392, -104.9903, America/Denver

Open-Meteo Ensemble API endpoint:
  https://ensemble-api.open-meteo.com/v1/ensemble
  params: latitude, longitude, hourly=temperature_2m, models=gfs_seamless,
          temperature_unit=fahrenheit, timezone=<local_tz>
  Returns: hourly.temperature_2m_member00 through temperature_2m_member30 (31 members)
"""

import requests
import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# City configurations: lat, lon, timezone
CITY_CONFIGS = {
    "NYC":     {"lat": 40.7829, "lon": -73.9654, "tz": "America/New_York"},
    "Chicago": {"lat": 41.9742, "lon": -87.9073, "tz": "America/Chicago"},
    "Miami":   {"lat": 25.7617, "lon": -80.1918, "tz": "America/New_York"},
    "LA":      {"lat": 34.0522, "lon": -118.2437, "tz": "America/Los_Angeles"},
    "Denver":  {"lat": 39.7392, "lon": -104.9903, "tz": "America/Denver"},
}

# Kalshi city code → our city name mapping
KALSHI_CITY_MAP = {
    "NY": "NYC",
    "CHI": "Chicago",
    "MIA": "Miami",
    "LAX": "LA",
    "DEN": "Denver",
}

OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


def get_ensemble_forecast(city: str, forecast_days: int = 2) -> List[float]:
    """
    Fetch GFS ensemble forecast for a city and extract daily high temps.

    Args:
        city: City name (NYC, Chicago, Miami, LA, Denver)
        forecast_days: Number of forecast days to fetch (default 2)

    Returns:
        List of 31 daily high temperature values (one per ensemble member)
        Each value is the max hourly temp for the target day in °F.
    """
    config = CITY_CONFIGS.get(city)
    if not config:
        logger.error(f"[WEATHER_FORECAST] Unknown city: {city}")
        return []

    params = {
        "latitude": config["lat"],
        "longitude": config["lon"],
        "hourly": "temperature_2m",
        "models": "gfs_seamless",
        "forecast_days": forecast_days,
        "temperature_unit": "fahrenheit",
        "timezone": config["tz"],
    }

    try:
        resp = requests.get(OPEN_METEO_ENSEMBLE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"[WEATHER_FORECAST] Open-Meteo API failed for {city}: {e}")
        return []

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    # Find ensemble member columns (temperature_2m_member00 to temperature_2m_member30)
    member_keys = [k for k in hourly.keys() if k.startswith("temperature_2m_member")]
    member_keys.sort()

    if not member_keys:
        logger.error(f"[WEATHER_FORECAST] No ensemble member data found for {city}")
        return []

    # We want the daily high for tomorrow (forecast_day=1, which is index 1 if we fetched 2 days)
    # The times are in local timezone. We want the max temp between 06:00-23:00 for the target day.
    # For simplicity, we'll take the max of all hourly values for the second day (indices 24-47)
    # This covers the full day regardless of exact sunrise/sunset.
    day_start_idx = 24  # Start of second day (tomorrow)
    day_end_idx = min(48, len(times))  # End of second day or data limit

    if day_start_idx >= len(times):
        logger.error(f"[WEATHER_FORECAST] Not enough forecast hours for {city}")
        return []

    daily_highs = []
    for member_key in member_keys:
        temps = hourly[member_key]
        if not temps:
            continue
        # Extract temps for the target day
        day_temps = temps[day_start_idx:day_end_idx]
        # Filter out None values
        day_temps = [t for t in day_temps if t is not None]
        if day_temps:
            daily_highs.append(max(day_temps))

    logger.info(
        f"[WEATHER_FORECAST] {city}: fetched {len(member_keys)} ensemble members, "
        f"{len(daily_highs)} valid daily highs. Range: {min(daily_highs):.1f}°F - {max(daily_highs):.1f}°F "
        f"(mean: {sum(daily_highs)/len(daily_highs):.1f}°F)"
    )

    return daily_highs


def get_ensemble_forecast_for_city_code(city_code: str, forecast_days: int = 2) -> List[float]:
    """
    Fetch ensemble forecast using Kalshi city code (NY, CHI, MIA, LAX, DEN).

    Args:
        city_code: Kalshi city code
        forecast_days: Number of forecast days

    Returns:
        List of daily high temperatures
    """
    city_name = KALSHI_CITY_MAP.get(city_code)
    if not city_name:
        logger.error(f"[WEATHER_FORECAST] Unknown Kalshi city code: {city_code}")
        return []
    return get_ensemble_forecast(city_name, forecast_days)


def get_ensemble_probability(daily_highs: List[float], threshold: float, comparison: str = "above") -> float:
    """
    Calculate probability from ensemble daily highs.

    Args:
        daily_highs: List of daily high temperatures from ensemble members
        threshold: Temperature threshold
        comparison: 'above' or 'below'

    Returns:
        Probability as float (0.0 to 1.0)
    """
    if not daily_highs:
        return 0.5

    if comparison == "above":
        count = sum(1 for t in daily_highs if t > threshold)
    elif comparison == "below":
        count = sum(1 for t in daily_highs if t < threshold)
    elif comparison == "above_or_equal":
        count = sum(1 for t in daily_highs if t >= threshold)
    elif comparison == "below_or_equal":
        count = sum(1 for t in daily_highs if t <= threshold)
    else:
        logger.warning(f"[WEATHER_FORECAST] Unknown comparison: {comparison}, defaulting to 'above'")
        count = sum(1 for t in daily_highs if t > threshold)

    return count / len(daily_highs)


def get_ensemble_bin_probability(daily_highs: List[float], low: float, high: float) -> float:
    """
    Calculate probability of temperature falling within a bin [low, high].

    Args:
        daily_highs: List of daily high temperatures
        low: Lower bound (inclusive)
        high: Upper bound (inclusive)

    Returns:
        Probability as float (0.0 to 1.0)
    """
    if not daily_highs:
        return 0.0

    count = sum(1 for t in daily_highs if low <= t <= high)
    return count / len(daily_highs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Quick test
    for city in ["NYC", "Chicago", "Miami", "LA", "Denver"]:
        highs = get_ensemble_forecast(city)
        if highs:
            print(f"\n{city}: {len(highs)} members")
            print(f"  Range: {min(highs):.1f}°F - {max(highs):.1f}°F")
            print(f"  Mean: {sum(highs)/len(highs):.1f}°F")
            # Example probability above 70°F
            p_above_70 = get_ensemble_probability(highs, 70.0, "above")
            print(f"  P(above 70°F) = {p_above_70:.1%}")
