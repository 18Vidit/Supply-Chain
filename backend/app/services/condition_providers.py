"""Real-world condition providers with privacy-safe offline fallbacks.

Open-Meteo is used for weather without an API key. TomTom Traffic Flow is
supported when TOMTOM_API_KEY is configured. OSRM supplies road-network route
duration and distance; for ocean/intermodal lanes, the engine falls back to
physical distance plus port/customs buffers instead of random movement.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from ..domain.global_network import Coordinate, haversine_km, route_distance_km

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving/{coordinates}"
TOMTOM_FLOW_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

CACHE_TTL_SEC = int(os.getenv("CONDITIONS_CACHE_TTL_SEC", "600"))
REQUEST_TIMEOUT_SEC = float(os.getenv("CONDITIONS_TIMEOUT_SEC", "5"))

_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def get_weather_condition(lat: float, lng: float) -> Dict[str, Any]:
    """Return current and near-term weather impact for a location."""
    key = f"weather:{round(lat, 2)}:{round(lng, 2)}"
    cached = _get_cached(key)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lng,
        "current": "temperature_2m,wind_speed_10m,precipitation,weather_code",
        "hourly": "wind_speed_10m,precipitation,visibility",
        "forecast_days": 1,
        "timezone": "UTC",
    }

    try:
        response = requests.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})
        hourly = data.get("hourly", {})
        winds = _numbers(hourly.get("wind_speed_10m") or hourly.get("windspeed_10m"))
        rain = _numbers(hourly.get("precipitation"))
        visibility = _numbers(hourly.get("visibility"))

        wind_peak = max(winds) if winds else _as_float(current.get("wind_speed_10m"))
        rain_peak = max(rain) if rain else _as_float(current.get("precipitation"))
        visibility_min = min(visibility) if visibility else 50000.0
        delay_factor = _weather_delay_factor(wind_peak, rain_peak, visibility_min)
        result = {
            "source": "open-meteo",
            "lat": round(float(lat), 4),
            "lng": round(float(lng), 4),
            "temperature_c": _as_float(current.get("temperature_2m")),
            "wind_kmh": round(wind_peak, 1),
            "precipitation_mm": round(rain_peak, 2),
            "visibility_m": round(visibility_min, 0),
            "weather_code": current.get("weather_code"),
            "delay_factor": delay_factor,
            "risk_label": _weather_label(delay_factor),
            "updated_at": data.get("current", {}).get("time"),
        }
        _set_cached(key, result)
        return result
    except Exception as exc:
        logger.debug("Open-Meteo unavailable for %.2f,%.2f: %s", lat, lng, exc)
        result = _offline_weather_condition(lat, lng)
        _set_cached(key, result)
        return result


def get_traffic_condition(lat: float, lng: float) -> Dict[str, Any]:
    """Return traffic flow data for the nearest road when a traffic key exists."""
    key = f"traffic:{round(lat, 3)}:{round(lng, 3)}"
    cached = _get_cached(key)
    if cached:
        return cached

    api_key = os.getenv("TOMTOM_API_KEY", "").strip()
    if api_key:
        try:
            response = requests.get(
                TOMTOM_FLOW_URL,
                params={"key": api_key, "point": f"{lat},{lng}", "unit": "KMPH"},
                timeout=REQUEST_TIMEOUT_SEC,
            )
            response.raise_for_status()
            flow = response.json().get("flowSegmentData", {})
            current_speed = _as_float(flow.get("currentSpeed"))
            free_flow_speed = _as_float(flow.get("freeFlowSpeed"), default=current_speed or 1.0)
            current_time = _as_float(flow.get("currentTravelTime"))
            free_flow_time = _as_float(flow.get("freeFlowTravelTime"), default=current_time or 1.0)
            congestion = _clamp(1.0 - (current_speed / max(free_flow_speed, 1.0))) if current_speed else 0.0
            result = {
                "source": "tomtom",
                "current_speed_kmh": round(current_speed, 1),
                "free_flow_speed_kmh": round(free_flow_speed, 1),
                "current_travel_time_sec": round(current_time, 1),
                "free_flow_travel_time_sec": round(free_flow_time, 1),
                "congestion_index": round(congestion, 3),
                "road_closure": bool(flow.get("roadClosure", False)),
                "confidence": _as_float(flow.get("confidence"), default=0.0),
            }
            _set_cached(key, result)
            return result
        except Exception as exc:
            logger.debug("TomTom traffic unavailable for %.2f,%.2f: %s", lat, lng, exc)

    result = {
        "source": "offline-traffic-proxy",
        "current_speed_kmh": 0.0,
        "free_flow_speed_kmh": 0.0,
        "current_travel_time_sec": 0.0,
        "free_flow_travel_time_sec": 0.0,
        "congestion_index": 0.0,
        "road_closure": False,
        "confidence": 0.0,
        "note": "Set TOMTOM_API_KEY to enable live traffic flow.",
    }
    _set_cached(key, result)
    return result


def get_osrm_route(points: Iterable[Coordinate]) -> Dict[str, Any]:
    """Call OSRM for real road geometry, distance, and duration."""
    point_list = list(points)
    if len(point_list) < 2:
        return {"source": "invalid", "coordinates": point_list, "distance_km": 0.0, "duration_min": 0.0}

    coord_string = ";".join(f"{lng},{lat}" for lat, lng in point_list)
    key = f"osrm:{coord_string}"
    cached = _get_cached(key)
    if cached:
        return cached

    try:
        response = requests.get(
            OSRM_ROUTE_URL.format(coordinates=coord_string),
            params={"overview": "full", "geometries": "geojson", "alternatives": "false"},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise ValueError(data.get("message") or data.get("code") or "No route")
        route = data["routes"][0]
        coordinates = [(lat_lng[1], lat_lng[0]) for lat_lng in route["geometry"]["coordinates"]]
        result = {
            "source": "osrm",
            "coordinates": coordinates,
            "distance_km": round(float(route["distance"]) / 1000.0, 2),
            "duration_min": round(float(route["duration"]) / 60.0, 1),
        }
        _set_cached(key, result)
        return result
    except Exception as exc:
        logger.debug("OSRM route unavailable: %s", exc)
        distance = route_distance_km(point_list)
        result = {
            "source": "haversine-fallback",
            "coordinates": point_list,
            "distance_km": round(distance, 2),
            "duration_min": round((distance / 55.0) * 60.0, 1),
        }
        _set_cached(key, result)
        return result


def assess_route_conditions(points: Iterable[Coordinate], mode: str = "road") -> Dict[str, Any]:
    """Combine weather, traffic, and route metrics into one route impact profile."""
    point_list = list(points)
    if not point_list:
        return {"source": "none", "condition_score": 0.0, "delay_multiplier": 1.0}

    sample_points = _sample_points(point_list)
    weather_samples = [get_weather_condition(lat, lng) for lat, lng in sample_points]
    traffic_samples = [get_traffic_condition(lat, lng) for lat, lng in sample_points] if mode == "road" else []
    route = get_osrm_route(point_list) if mode == "road" else _non_road_route(point_list, mode)

    max_weather = max((sample.get("delay_factor", 0.0) for sample in weather_samples), default=0.0)
    avg_weather = sum(sample.get("delay_factor", 0.0) for sample in weather_samples) / max(len(weather_samples), 1)
    max_congestion = max((sample.get("congestion_index", 0.0) for sample in traffic_samples), default=0.0)
    road_closed = any(sample.get("road_closure") for sample in traffic_samples)

    condition_score = _clamp((0.55 * max_weather) + (0.25 * avg_weather) + (0.20 * max_congestion))
    if road_closed:
        condition_score = max(condition_score, 0.92)

    delay_multiplier = round(1.0 + condition_score * (0.55 if mode == "road" else 0.35), 3)
    return {
        "source": "open-meteo+tomtom+osrm",
        "mode": mode,
        "route": route,
        "weather": weather_samples,
        "traffic": traffic_samples,
        "condition_score": round(condition_score, 3),
        "delay_multiplier": delay_multiplier,
        "road_closure": road_closed,
        "risk_label": _route_condition_label(condition_score),
    }


def _non_road_route(points: List[Coordinate], mode: str) -> Dict[str, Any]:
    distance = route_distance_km(points)
    speed = 34.0 if mode == "ocean" else 48.0
    return {
        "source": "global-lane-distance",
        "coordinates": points,
        "distance_km": round(distance, 2),
        "duration_min": round((distance / speed) * 60.0, 1),
    }


def _sample_points(points: List[Coordinate]) -> List[Coordinate]:
    if len(points) <= 3:
        return points
    return [points[0], points[len(points) // 2], points[-1]]


def _weather_delay_factor(wind_kmh: float, precipitation_mm: float, visibility_m: float) -> float:
    wind_score = _clamp((wind_kmh - 35.0) / 65.0)
    rain_score = _clamp(precipitation_mm / 30.0)
    visibility_score = _clamp((1800.0 - visibility_m) / 1800.0)
    return round(_clamp(max(wind_score, rain_score, visibility_score)), 3)


def _weather_label(score: float) -> str:
    if score >= 0.75:
        return "severe"
    if score >= 0.45:
        return "elevated"
    if score >= 0.2:
        return "watch"
    return "clear"


def _route_condition_label(score: float) -> str:
    if score >= 0.75:
        return "CRITICAL"
    if score >= 0.5:
        return "HIGH"
    if score >= 0.25:
        return "MODERATE"
    return "LOW"


def _offline_weather_condition(lat: float, lng: float) -> Dict[str, Any]:
    """Deterministic fallback used when the network is down."""
    monsoon_band = 1.0 if -5 <= lat <= 25 and 65 <= lng <= 120 else 0.0
    winter_band = 1.0 if lat > 45 else 0.0
    coastal = 1.0 if abs(lng) > 115 or -5 <= lat <= 5 else 0.0
    delay_factor = round(_clamp(0.12 + 0.18 * monsoon_band + 0.08 * winter_band + 0.05 * coastal), 3)
    return {
        "source": "offline-climatology",
        "lat": round(float(lat), 4),
        "lng": round(float(lng), 4),
        "temperature_c": 0.0,
        "wind_kmh": 0.0,
        "precipitation_mm": 0.0,
        "visibility_m": 50000,
        "weather_code": None,
        "delay_factor": delay_factor,
        "risk_label": _weather_label(delay_factor),
        "updated_at": None,
    }


def _numbers(values: Optional[Iterable[Any]]) -> List[float]:
    return [_as_float(value) for value in values or [] if value is not None]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _get_cached(key: str) -> Optional[Dict[str, Any]]:
    cached = _cache.get(key)
    if not cached:
        return None
    ts, value = cached
    if time.time() - ts > CACHE_TTL_SEC:
        _cache.pop(key, None)
        return None
    return value


def _set_cached(key: str, value: Dict[str, Any]) -> None:
    _cache[key] = (time.time(), value)
