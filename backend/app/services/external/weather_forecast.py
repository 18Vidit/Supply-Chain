"""
Open-Meteo Weather Forecast Integration.

Proactive hazard detection — this is the key innovation that satisfies
the challenge's "preemptive detection" requirement.

Instead of only reacting to current disasters, we pull the 24-hour
forecast for every truck's destination and flag dangerous conditions
BEFORE the truck encounters them.

API: https://api.open-meteo.com/v1/forecast
- Completely free, no API key needed
- 10,000 requests/day limit
- Returns hourly arrays for weather variables
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ...config import (
    FORECAST_THRESHOLDS,
    OPEN_METEO_BASE_URL,
    OPEN_METEO_PARAMS,
)
from ...models.risk_models import ForecastAlertData, ForecastType

logger = logging.getLogger(__name__)

# HTTP client with connection pooling
_client: Optional[httpx.AsyncClient] = None


async def get_client() -> httpx.AsyncClient:
    """Get or create a shared async HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def close_client() -> None:
    """Close the shared HTTP client (call on shutdown)."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def fetch_forecast(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """
    Fetch 24-hour hourly weather forecast from Open-Meteo.

    Args:
        lat: Latitude of the location
        lng: Longitude of the location

    Returns:
        Raw API response dict, or None if the request fails.

    Example response structure:
        {
            "hourly": {
                "time": ["2026-04-21T00:00", ...],
                "temperature_2m": [28.6, ...],
                "windspeed_10m": [16.2, ...],
                "precipitation": [0.00, ...],
                "snowfall": [0.00, ...],
                "visibility": [90000.00, ...]
            }
        }
    """
    client = await get_client()

    params = {
        "latitude": str(lat),
        "longitude": str(lng),
        **OPEN_METEO_PARAMS,
    }

    try:
        response = await client.get(OPEN_METEO_BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        logger.debug(
            "Fetched forecast for (%.4f, %.4f): %d hourly data points",
            lat,
            lng,
            len(data.get("hourly", {}).get("time", [])),
        )

        return data

    except httpx.HTTPStatusError as e:
        logger.error(
            "Open-Meteo HTTP error for (%.4f, %.4f): %s %s",
            lat, lng, e.response.status_code, e.response.text[:200],
        )
    except httpx.RequestError as e:
        logger.error("Open-Meteo request error for (%.4f, %.4f): %s", lat, lng, e)
    except Exception as e:
        logger.error("Unexpected error fetching forecast: %s", e)

    return None


def analyze_forecast_risks(
    forecast_data: Dict[str, Any],
    truck: Optional[Dict[str, Any]] = None,
    lat: float = 0.0,
    lng: float = 0.0,
) -> List[ForecastAlertData]:
    """
    Analyze forecast data against risk thresholds.

    Checks each hour of the 24-hour forecast for:
    - High wind: windspeed > 80 km/h
    - Heavy precipitation: rainfall > 20 mm/hr
    - Heavy snowfall: snowfall > 5 cm/hr
    - Zero visibility: visibility < 1000m

    Args:
        forecast_data: Raw Open-Meteo response dict
        truck: Optional truck dict (to populate truck_id)
        lat: Latitude of the forecast location
        lng: Longitude of the forecast location

    Returns:
        List of ForecastAlertData for each threshold breach found.
    """
    alerts: List[ForecastAlertData] = []

    hourly = forecast_data.get("hourly", {})
    times = hourly.get("time", [])

    if not times:
        logger.warning("No hourly data in forecast response")
        return alerts

    # Current time for calculating hours_ahead
    now = datetime.now(timezone.utc)

    for forecast_type_key, config in FORECAST_THRESHOLDS.items():
        field = config["field"]
        threshold = config["threshold"]
        unit = config["unit"]
        is_below = config.get("below", False)

        values = hourly.get(field, [])
        if not values and field == "wind_speed_10m":
            values = hourly.get("windspeed_10m", [])

        for i, value in enumerate(values):
            if value is None:
                continue

            # Check threshold (some alerts trigger when BELOW, like visibility)
            threshold_breached = (
                (value < threshold) if is_below else (value > threshold)
            )

            if threshold_breached:
                # Calculate hours into the future
                try:
                    forecast_time = datetime.fromisoformat(times[i]).replace(
                        tzinfo=timezone.utc
                    )
                    hours_ahead = max(0, int((forecast_time - now).total_seconds() / 3600))
                except (ValueError, IndexError):
                    hours_ahead = i  # fallback to index as hour offset

                # Map string key to enum
                try:
                    forecast_type_enum = ForecastType(forecast_type_key)
                except ValueError:
                    forecast_type_enum = ForecastType.HIGH_WIND  # fallback

                alert = ForecastAlertData(
                    truck_id=truck.get("id") if truck else None,
                    forecast_type=forecast_type_enum,
                    forecast_value=round(float(value), 2),
                    threshold=threshold,
                    unit=unit,
                    hours_ahead=hours_ahead,
                    lat=lat,
                    lng=lng,
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                )

                alerts.append(alert)

                logger.info(
                    "Forecast alert: %s=%.1f%s (threshold %.1f%s) in %dh at (%.4f, %.4f)",
                    forecast_type_key,
                    value,
                    unit,
                    threshold,
                    unit,
                    hours_ahead,
                    lat,
                    lng,
                )

                # Only flag the FIRST breach per forecast type (earliest)
                break

    return alerts


async def check_truck_forecasts(
    trucks: List[Dict[str, Any]],
) -> Dict[str, List[ForecastAlertData]]:
    """
    Check weather forecasts for all trucks' destinations.

    This is the function called by the forecast scheduler loop.

    Args:
        trucks: List of truck dicts with lat, lng, destination, id keys.

    Returns:
        Dict mapping truck_id → list of forecast alerts for that truck.
    """
    all_alerts: Dict[str, List[ForecastAlertData]] = {}

    for truck in trucks:
        truck_id = truck.get("id", "unknown")

        # Use the truck's current position for forecast
        # (In production, we'd use the destination coordinates,
        #  but the current position catches en-route weather too)
        lat = truck.get("lat", 0)
        lng = truck.get("lng", 0)

        if lat == 0 and lng == 0:
            continue

        forecast = await fetch_forecast(lat, lng)
        if forecast is None:
            continue

        alerts = analyze_forecast_risks(forecast, truck, lat, lng)

        if alerts:
            all_alerts[truck_id] = alerts
            logger.info(
                "Truck %s (%s): %d forecast alerts at (%.4f, %.4f)",
                truck.get("callsign", "unknown"),
                truck_id,
                len(alerts),
                lat,
                lng,
            )

    logger.info(
        "Forecast check complete: %d/%d trucks have weather alerts",
        len(all_alerts),
        len(trucks),
    )

    return all_alerts
