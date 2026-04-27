"""
AirNow Air Quality Index (AQI) Integration.

Provides AQI data to the risk engine to penalize routes through
hazardous smoke plumes even when they're outside the wildfire polygon.

API: https://www.airnowapi.org/
- Free with registration
- Returns current AQI for any US lat/lng coordinates
- Key set via AIRNOW_API_KEY environment variable

Graceful fallback: If no API key is configured or the request fails,
returns AQI=0 (no penalty). The system works without it.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from ...config import (
    AQI_CACHE_TTL_SEC,
    AIRNOW_API_KEY,
    AIRNOW_BASE_URL,
)
from ...models.risk_models import AQIData

logger = logging.getLogger(__name__)

# In-memory cache: (lat_rounded, lng_rounded) → (AQIData, timestamp)
_aqi_cache: Dict[Tuple[float, float], Tuple[AQIData, float]] = {}

# HTTP client
_client: Optional[httpx.AsyncClient] = None


async def get_client() -> httpx.AsyncClient:
    """Get or create a shared async HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def close_client() -> None:
    """Close the shared HTTP client."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def fetch_aqi(lat: float, lng: float) -> AQIData:
    """
    Fetch current AQI for a location from AirNow.

    Results are cached for AQI_CACHE_TTL_SEC (default 5 minutes)
    to avoid excessive API calls. Coordinates are rounded to 2
    decimal places for cache key (≈1.1km precision, good enough for AQI).

    Args:
        lat: Latitude
        lng: Longitude

    Returns:
        AQIData with current AQI value. Returns AQI=0 on any failure.
    """
    # Check if API key is configured
    if not AIRNOW_API_KEY:
        logger.debug("AirNow API key not configured — returning AQI=0")
        return AQIData(aqi=0, category="Not Available", lat=lat, lng=lng)

    # Check cache
    cache_key = (round(lat, 2), round(lng, 2))
    cached = _aqi_cache.get(cache_key)
    if cached:
        cached_data, cached_time = cached
        if time.time() - cached_time < AQI_CACHE_TTL_SEC:
            logger.debug(
                "AQI cache hit for (%.2f, %.2f): AQI=%d",
                lat, lng, cached_data.aqi,
            )
            return cached_data

    # Fetch from API
    client = await get_client()

    params = {
        "format": "application/json",
        "latitude": str(lat),
        "longitude": str(lng),
        "distance": "50",  # search radius in miles
        "API_KEY": AIRNOW_API_KEY,
    }

    try:
        response = await client.get(AIRNOW_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        if not data or not isinstance(data, list):
            logger.debug("Empty AQI response for (%.4f, %.4f)", lat, lng)
            return AQIData(aqi=0, category="No Data", lat=lat, lng=lng)

        # Find the highest AQI observation (usually PM2.5 or O3)
        max_aqi_entry = max(data, key=lambda x: x.get("AQI", 0))

        result = AQIData(
            aqi=max_aqi_entry.get("AQI", 0),
            category=max_aqi_entry.get("Category", {}).get("Name", "Unknown"),
            parameter=max_aqi_entry.get("ParameterName", "PM2.5"),
            lat=lat,
            lng=lng,
            reporting_area=max_aqi_entry.get("ReportingArea", ""),
        )

        # Cache the result
        _aqi_cache[cache_key] = (result, time.time())

        logger.info(
            "AQI for (%.4f, %.4f): %d (%s) — %s",
            lat, lng, result.aqi, result.category, result.reporting_area,
        )

        return result

    except httpx.HTTPStatusError as e:
        logger.warning(
            "AirNow HTTP error for (%.4f, %.4f): %s",
            lat, lng, e.response.status_code,
        )
    except httpx.RequestError as e:
        logger.warning("AirNow request error for (%.4f, %.4f): %s", lat, lng, e)
    except Exception as e:
        logger.warning("Unexpected AirNow error: %s", e)

    # Graceful fallback — system works without AQI data
    return AQIData(aqi=0, category="Error", lat=lat, lng=lng)


async def fetch_aqi_batch(
    coordinates: list[Tuple[float, float]],
) -> Dict[str, AQIData]:
    """
    Fetch AQI for multiple coordinates.

    Returns a dict mapping "lat,lng" → AQIData.
    Useful for building the aqi_cache for evaluate_all_risks().
    """
    results: Dict[str, AQIData] = {}

    for lat, lng in coordinates:
        key = f"{round(lat, 2)},{round(lng, 2)}"
        if key not in results:
            results[key] = await fetch_aqi(lat, lng)

    logger.info(
        "Batch AQI fetch: %d unique locations queried",
        len(results),
    )

    return {k: v.model_dump() for k, v in results.items()}


def clear_cache() -> None:
    """Clear the AQI cache (useful for testing)."""
    _aqi_cache.clear()
    logger.debug("AQI cache cleared")
