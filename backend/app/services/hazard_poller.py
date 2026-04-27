"""Live global hazard aggregation for logistics lanes."""

from __future__ import annotations

import logging
import math
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import requests

from ..domain.global_network import LANES, PORTS

logger = logging.getLogger(__name__)

SEVERITY_WEIGHTS = {
    "wildfire": 1.00,
    "tropical_cyclone": 0.96,
    "major_earthquake": 0.95,
    "flash_flood": 0.90,
    "severe_storm": 0.85,
    "winter_storm": 0.75,
    "moderate_earthquake": 0.70,
    "high_wind": 0.60,
    "flood_watch": 0.55,
    "dense_fog": 0.50,
    "heavy_rain": 0.72,
    "unknown": 0.40,
}

_hazard_cache: List[Dict[str, Any]] = []
_cache_ts: float = 0.0
CACHE_TTL = 300


def get_all_hazards() -> List[Dict[str, Any]]:
    """Return cached live hazards and deterministic offline fallback hazards."""
    global _hazard_cache, _cache_ts

    now = _time.time()
    if _hazard_cache and (now - _cache_ts) < CACHE_TTL:
        return _hazard_cache

    live: List[Dict[str, Any]] = []
    live.extend(_fetch_eonet())
    live.extend(_fetch_usgs())
    live.extend(_fetch_openmeteo())

    _hazard_cache = live or _offline_baseline_hazards()
    _cache_ts = now
    return _hazard_cache


def _fetch_eonet() -> List[Dict[str, Any]]:
    try:
        response = requests.get("https://eonet.gsfc.nasa.gov/api/v3/events", params={"status": "open", "limit": 30}, timeout=4)
        response.raise_for_status()
        events = response.json().get("events", [])
    except Exception as exc:
        logger.debug("EONET unavailable: %s", exc)
        return []

    hazards: List[Dict[str, Any]] = []
    for event in events:
        geometry_items = event.get("geometry") or []
        if not geometry_items:
            continue
        g0 = geometry_items[-1]
        coords = g0.get("coordinates")
        if not coords:
            continue
        category = (event.get("categories") or [{}])[0].get("id", "unknown")
        event_type = _map_eonet_category(category)
        lat, lng = _centroid_from_eonet_geometry(g0)
        if lat is None or lng is None:
            continue
        radius = 65.0 if event_type != "tropical_cyclone" else 180.0
        hazards.append(
            _hazard(
                hazard_id=f"eonet-{event.get('id')}",
                source="nasa-eonet",
                event_type=event_type,
                title=event.get("title", "NASA EONET event"),
                lat=lat,
                lng=lng,
                radius_km=radius,
                severity=SEVERITY_WEIGHTS.get(event_type, 0.5),
            )
        )
    return hazards


def _fetch_usgs() -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://earthquake.usgs.gov/fdsnws/event/1/query",
            params={"format": "geojson", "minmagnitude": 4.5, "limit": 20, "orderby": "time"},
            timeout=4,
        )
        response.raise_for_status()
        features = response.json().get("features", [])
    except Exception as exc:
        logger.debug("USGS unavailable: %s", exc)
        return []

    hazards: List[Dict[str, Any]] = []
    for feature in features:
        props = feature.get("properties", {})
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lng, lat = float(coords[0]), float(coords[1])
        mag = float(props.get("mag") or 4.5)
        event_type = "major_earthquake" if mag >= 6.0 else "moderate_earthquake"
        hazards.append(
            _hazard(
                hazard_id=f"usgs-{feature.get('id')}",
                source="usgs-earthquake",
                event_type=event_type,
                title=f"M{mag:.1f} earthquake - {props.get('place', 'Unknown')}",
                lat=lat,
                lng=lng,
                radius_km=55.0 if mag < 6.0 else 120.0,
                severity=SEVERITY_WEIGHTS[event_type],
            )
        )
    return hazards


def _fetch_openmeteo() -> List[Dict[str, Any]]:
    hazards: List[Dict[str, Any]] = []
    for lat, lng, label in _weather_watch_points():
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "hourly": "wind_speed_10m,precipitation,visibility",
                    "forecast_days": 1,
                    "timezone": "UTC",
                },
                timeout=3,
            )
            response.raise_for_status()
            hourly = response.json().get("hourly", {})
            winds = _numbers(hourly.get("wind_speed_10m") or hourly.get("windspeed_10m"))
            rain = _numbers(hourly.get("precipitation"))
            visibility = _numbers(hourly.get("visibility"))
        except Exception as exc:
            logger.debug("Open-Meteo unavailable for %s: %s", label, exc)
            continue

        max_wind = max(winds) if winds else 0.0
        max_rain = max(rain) if rain else 0.0
        min_visibility = min(visibility) if visibility else 50000.0

        if max_wind >= 55:
            hazards.append(_hazard(f"weather-wind-{label}", "open-meteo", "high_wind", f"High wind forecast - {label} {max_wind:.0f} km/h", lat, lng, 75.0, 0.62))
        if max_rain >= 18:
            hazards.append(_hazard(f"weather-rain-{label}", "open-meteo", "heavy_rain", f"Heavy rain forecast - {label} {max_rain:.0f} mm/hr", lat, lng, 80.0, 0.72))
        if min_visibility <= 1200:
            hazards.append(_hazard(f"weather-fog-{label}", "open-meteo", "dense_fog", f"Low visibility forecast - {label} {min_visibility:.0f} m", lat, lng, 55.0, 0.52))
    return hazards


def _weather_watch_points() -> List[Tuple[float, float, str]]:
    points = [(port.lat, port.lng, port.code.lower()) for port in PORTS]
    for lane in LANES:
        mid = lane.points[len(lane.points) // 2]
        points.append((mid[0], mid[1], lane.id.replace("lane-", "")))
    return points[:10]


def _offline_baseline_hazards() -> List[Dict[str, Any]]:
    """Deterministic offline baseline, used only when live providers are unavailable."""
    return [
        _hazard("baseline-arabian-sea-monsoon", "offline-risk-baseline", "heavy_rain", "Seasonal rain watch - Arabian Sea shipping lanes", 18.0, 66.0, 160.0, 0.50),
        _hazard("baseline-us-plains-wind", "offline-risk-baseline", "high_wind", "High wind watch - US central road corridor", 39.0, -99.0, 140.0, 0.46),
        _hazard("baseline-north-sea-fog", "offline-risk-baseline", "dense_fog", "Low visibility watch - North Sea approaches", 53.0, 4.0, 95.0, 0.42),
    ]


def _hazard(
    hazard_id: str,
    source: str,
    event_type: str,
    title: str,
    lat: float,
    lng: float,
    radius_km: float,
    severity: float,
) -> Dict[str, Any]:
    return {
        "id": hazard_id,
        "source": source,
        "source_api": source,
        "type": event_type,
        "event_type": event_type,
        "title": title,
        "severity_weight": round(float(severity), 2),
        "geometry": _make_circle_poly(lat, lng, max(radius_km / 111.0, 0.08)),
        "geometry_geojson": _make_circle_poly(lat, lng, max(radius_km / 111.0, 0.08)),
        "centroid_lat": round(float(lat), 4),
        "centroid_lng": round(float(lng), 4),
        "radius_km": round(float(radius_km), 1),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _map_eonet_category(category: str) -> str:
    value = category.lower()
    if "wildfire" in value:
        return "wildfire"
    if "storm" in value or "severe" in value:
        return "severe_storm"
    if "flood" in value:
        return "flash_flood"
    if "volcano" in value:
        return "unknown"
    if "ice" in value or "snow" in value:
        return "winter_storm"
    return "unknown"


def _centroid_from_eonet_geometry(geometry: Dict[str, Any]) -> Tuple[float | None, float | None]:
    coords = geometry.get("coordinates")
    if geometry.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return float(coords[1]), float(coords[0])
    flat = _flatten_coordinates(coords)
    if not flat:
        return None, None
    lat = sum(item[1] for item in flat) / len(flat)
    lng = sum(item[0] for item in flat) / len(flat)
    return lat, lng


def _flatten_coordinates(coords: Any) -> List[Tuple[float, float]]:
    if not isinstance(coords, list):
        return []
    if len(coords) >= 2 and all(isinstance(v, (int, float)) for v in coords[:2]):
        return [(float(coords[0]), float(coords[1]))]
    points: List[Tuple[float, float]] = []
    for item in coords:
        points.extend(_flatten_coordinates(item))
    return points


def _make_circle_poly(lat: float, lng: float, radius_deg: float = 0.5) -> Dict[str, Any]:
    coords = [
        [lng + radius_deg * math.cos(math.radians(angle)), lat + radius_deg * math.sin(math.radians(angle))]
        for angle in range(0, 360, 20)
    ]
    coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def _numbers(values: Any) -> List[float]:
    return [float(value) for value in values or [] if value is not None]
