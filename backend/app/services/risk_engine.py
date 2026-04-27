"""Risk scoring engine for truck and hazard pairs."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from shapely.geometry import Point, shape
except Exception:  # pragma: no cover - graceful runtime fallback
    Point = None
    shape = None

from ..config import (
    CARGO_PRIORITY_MULTIPLIER,
    DEGREES_TO_KM_APPROX,
    MAX_DETECTION_RADIUS_KM,
    SEVERITY_WEIGHTS,
    TIME_THRESHOLD_HOURS,
)
from ..models.risk_models import ComponentScores, RiskScoreResult

WEIGHT_PROXIMITY = 0.35
WEIGHT_VELOCITY = 0.30
WEIGHT_SEVERITY = 0.25
WEIGHT_AQI = 0.10
MIN_FLAGGED_SCORE = 0.40


def calculate_risk_score(
    truck: Mapping[str, Any],
    hazard: Mapping[str, Any],
    aqi_data: Optional[Mapping[str, Any]] = None,
) -> RiskScoreResult:
    """Calculate a weighted risk score for one truck-hazard pair."""
    truck_lat = _as_float(truck.get("lat"))
    truck_lng = _as_float(truck.get("lng"))
    speed_kmh = _as_float(truck.get("speed_kmh", truck.get("speed", 0.0)))

    proximity_km, has_valid_area = _distance_to_hazard_km(truck, hazard)
    proximity_score = _proximity_score(proximity_km, has_valid_area)

    hazard_lat = _as_float(hazard.get("centroid_lat", truck_lat))
    hazard_lng = _as_float(hazard.get("centroid_lng", truck_lng))
    centroid_distance_km = _haversine_km(truck_lat, truck_lng, hazard_lat, hazard_lng)

    heading = _as_float(truck.get("heading_deg", truck.get("heading", 0.0)))
    bearing_to_hazard = _bearing_degrees(truck_lat, truck_lng, hazard_lat, hazard_lng)
    heading_delta = _angle_delta(heading, bearing_to_hazard)
    is_approaching = proximity_km == 0.0 or heading_delta <= 90.0

    eta_to_hazard_min = _eta_minutes(proximity_km if proximity_km > 0 else centroid_distance_km, speed_kmh)
    velocity_score = _velocity_score(eta_to_hazard_min, is_approaching, speed_kmh, proximity_km)

    severity_score = _severity_score(hazard)
    aqi_score = _aqi_score(aqi_data)

    raw_score = (
        WEIGHT_PROXIMITY * proximity_score
        + WEIGHT_VELOCITY * velocity_score
        + WEIGHT_SEVERITY * severity_score
        + WEIGHT_AQI * aqi_score
    )
    cargo_priority = int(_as_float(truck.get("cargo_priority", 1), default=1.0))
    cargo_multiplier = CARGO_PRIORITY_MULTIPLIER.get(cargo_priority, 1.0)
    risk_score = _clamp(raw_score * cargo_multiplier)

    return RiskScoreResult(
        risk_score=round(risk_score, 4),
        risk_level=RiskScoreResult.classify_risk(risk_score),
        eta_to_hazard_min=int(eta_to_hazard_min),
        proximity_km=round(max(0.0, proximity_km), 2),
        component_scores=ComponentScores(
            proximity=round(proximity_score, 4),
            velocity=round(velocity_score, 4),
            severity=round(severity_score, 4),
            aqi=round(aqi_score, 4),
        ),
        is_approaching=is_approaching,
        raw_score=round(raw_score, 4),
        cargo_multiplier=round(cargo_multiplier, 2),
    )


def evaluate_all_risks(
    trucks: Iterable[Mapping[str, Any]],
    hazards: Iterable[Mapping[str, Any]],
    aqi_cache: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate all truck-hazard combinations and return flagged results."""
    results: List[Dict[str, Any]] = []
    hazards_list = list(hazards)

    if not hazards_list:
        return []

    for truck in trucks:
        aqi_data = _aqi_for_truck(truck, aqi_cache or {})
        for hazard in hazards_list:
            score = calculate_risk_score(truck, hazard, aqi_data)
            if score.risk_score < MIN_FLAGGED_SCORE:
                continue
            results.append(
                {
                    "truck_id": str(truck.get("id", "")),
                    "truck_callsign": truck.get("callsign", str(truck.get("id", ""))),
                    "hazard_id": str(hazard.get("id", "")),
                    "hazard_title": hazard.get("title", hazard.get("event_type", hazard.get("type", "Hazard"))),
                    "result": score,
                }
            )

    results.sort(key=lambda item: item["result"].risk_score, reverse=True)
    return results


def evaluate_risk() -> List[Dict[str, Any]]:
    """Evaluate the live simulator fleet and return one summary row per truck."""
    from .hazard_poller import get_all_hazards
    from ..simulator.truck_simulator import get_trucks

    trucks = get_trucks()
    hazards = list(get_all_hazards())

    if not hazards:
        return [_low_risk_summary(truck) for truck in trucks]

    summaries: List[Dict[str, Any]] = []
    for truck in trucks:
        best_hazard: Optional[Mapping[str, Any]] = None
        best_score: Optional[RiskScoreResult] = None
        for hazard in hazards:
            score = calculate_risk_score(truck, hazard)
            if best_score is None or score.risk_score > best_score.risk_score:
                best_hazard = hazard
                best_score = score

        if best_score is None or best_hazard is None:
            summaries.append(_low_risk_summary(truck))
            continue

        summaries.append(
            {
                "truck_id": str(truck.get("id", "")),
                "callsign": truck.get("callsign", str(truck.get("id", ""))),
                "risk_score": best_score.risk_score,
                "risk_label": best_score.risk_level.value,
                "risk_level": best_score.risk_level.value,
                "hazard_id": str(best_hazard.get("id", "")),
                "hazard_title": best_hazard.get("title", "Hazard"),
                "proximity_km": best_score.proximity_km,
                "eta_min": best_score.eta_to_hazard_min,
                "is_approaching": best_score.is_approaching,
                "component_scores": best_score.component_scores.model_dump(),
            }
        )

    summaries.sort(key=lambda item: item["risk_score"], reverse=True)
    return summaries


def _distance_to_hazard_km(
    truck: Mapping[str, Any],
    hazard: Mapping[str, Any],
) -> Tuple[float, bool]:
    truck_lat = _as_float(truck.get("lat"))
    truck_lng = _as_float(truck.get("lng"))
    hazard_lat = _as_float(hazard.get("centroid_lat", truck_lat))
    hazard_lng = _as_float(hazard.get("centroid_lng", truck_lng))
    centroid_distance = _haversine_km(truck_lat, truck_lng, hazard_lat, hazard_lng)

    radius_km = hazard.get("radius_km")
    if radius_km is not None:
        return max(0.0, centroid_distance - _as_float(radius_km)), True

    geometry = hazard.get("geometry_geojson") or hazard.get("geometry")
    if not geometry or shape is None or Point is None:
        return centroid_distance, False

    try:
        polygon = shape(geometry)
        if polygon.is_empty or not polygon.is_valid:
            return centroid_distance, False
        point = Point(truck_lng, truck_lat)
        if polygon.contains(point) or polygon.touches(point):
            return 0.0, True
        return polygon.distance(point) * DEGREES_TO_KM_APPROX, True
    except Exception:
        return centroid_distance, False


def _proximity_score(distance_km: float, has_valid_area: bool) -> float:
    if not has_valid_area:
        return 0.0
    if distance_km <= 0:
        return 1.0
    if distance_km >= MAX_DETECTION_RADIUS_KM:
        return 0.0
    return _clamp(1.0 - (distance_km / MAX_DETECTION_RADIUS_KM))


def _velocity_score(eta_min: int, is_approaching: bool, speed_kmh: float, proximity_km: float) -> float:
    if proximity_km == 0.0 and speed_kmh > 0:
        return 1.0
    if not is_approaching or speed_kmh <= 0:
        return 0.0
    eta_hours = eta_min / 60.0
    if eta_hours >= TIME_THRESHOLD_HOURS:
        return 0.0
    return _clamp(1.0 - (eta_hours / TIME_THRESHOLD_HOURS))


def _severity_score(hazard: Mapping[str, Any]) -> float:
    explicit_weight = hazard.get("severity_weight")
    if explicit_weight is not None:
        return _clamp(_as_float(explicit_weight))
    event_type = str(hazard.get("event_type", hazard.get("type", "unknown")))
    return _clamp(SEVERITY_WEIGHTS.get(event_type, SEVERITY_WEIGHTS.get("unknown", 0.4)))


def _aqi_score(aqi_data: Optional[Mapping[str, Any]]) -> float:
    if not aqi_data:
        return 0.0
    aqi = int(_as_float(aqi_data.get("aqi", 0)))
    if aqi >= 301:
        return 1.0
    if aqi >= 201:
        return 0.75
    if aqi >= 151:
        return 0.5
    if aqi >= 101:
        return 0.25
    return 0.0


def _aqi_for_truck(truck: Mapping[str, Any], cache: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    if not cache:
        return None
    key = f"{round(_as_float(truck.get('lat')), 2)},{round(_as_float(truck.get('lng')), 2)}"
    value = cache.get(key)
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _eta_minutes(distance_km: float, speed_kmh: float) -> int:
    if distance_km <= 0:
        return 0
    if speed_kmh <= 0:
        return 9999
    return int(round((distance_km / speed_kmh) * 60.0))


def _low_risk_summary(truck: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "truck_id": str(truck.get("id", "")),
        "callsign": truck.get("callsign", str(truck.get("id", ""))),
        "risk_score": 0.0,
        "risk_label": "LOW",
        "risk_level": "LOW",
        "hazard_id": "",
        "hazard_title": "",
        "proximity_km": 0.0,
        "eta_min": 0,
        "is_approaching": False,
        "component_scores": {"proximity": 0.0, "velocity": 0.0, "severity": 0.0, "aqi": 0.0},
    }


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _bearing_degrees(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lng = math.radians(lng2 - lng1)
    y = math.sin(delta_lng) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lng)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _angle_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)
