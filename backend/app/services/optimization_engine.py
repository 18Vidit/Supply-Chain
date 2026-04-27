"""Route optimization, delay prediction, and dynamic rerouting."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..domain.global_network import Coordinate, get_lane, haversine_km, route_distance_km
from .condition_providers import assess_route_conditions


def optimize_route_for_truck(
    truck: Mapping[str, Any],
    disruption: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return ranked route options for a live truck."""
    lane = get_lane(str(truck.get("lane_id", "")))
    base_points = _truck_route_points(truck, lane)
    mode = str(truck.get("mode") or (lane or {}).get("mode") or "road")
    base_cost = float(truck.get("base_cost_usd") or (lane or {}).get("base_cost_usd") or 5000.0)
    planned_duration_min = float(truck.get("planned_duration_hours") or 8.0) * 60.0
    reliability = float(truck.get("reliability") or (lane or {}).get("reliability") or 0.8)
    customs_min = float(truck.get("customs_buffer_hours") or (lane or {}).get("customs_buffer_hours") or 0.0) * 60.0

    candidates = _candidate_routes(base_points, mode, disruption)
    ranked: List[Dict[str, Any]] = []

    for candidate in candidates:
        conditions = assess_route_conditions(candidate["points"], mode=mode)
        distance_km = float(conditions["route"].get("distance_km") or route_distance_km(candidate["points"]))
        duration_min = float(conditions["route"].get("duration_min") or (distance_km / 55.0 * 60.0))
        adjusted_duration_min = duration_min * float(conditions.get("delay_multiplier", 1.0)) + customs_min
        hazard_penalty = _hazard_penalty(candidate["points"], disruption)
        route_risk = _clamp(float(conditions.get("condition_score", 0.0)) + hazard_penalty)
        cost_usd = _estimate_cost(base_cost, distance_km, route_distance_km(base_points), route_risk, mode)
        delay_min = max(0.0, adjusted_duration_min - planned_duration_min)

        objective = _objective_score(
            duration_min=adjusted_duration_min,
            planned_duration_min=planned_duration_min,
            cost_usd=cost_usd,
            base_cost_usd=base_cost,
            route_risk=route_risk,
            reliability=reliability,
        )

        ranked.append(
            {
                "id": candidate["id"],
                "label": candidate["label"],
                "strategy": candidate["strategy"],
                "coordinates": [[round(lat, 5), round(lng, 5)] for lat, lng in conditions["route"]["coordinates"]],
                "distance_km": round(distance_km, 1),
                "duration_min": round(adjusted_duration_min, 1),
                "delay_min": round(delay_min, 1),
                "cost_usd": round(cost_usd, 2),
                "cost_delta_usd": round(cost_usd - base_cost, 2),
                "risk_index": round(route_risk, 3),
                "condition_label": conditions.get("risk_label", "LOW"),
                "condition_score": conditions.get("condition_score", 0.0),
                "delay_multiplier": conditions.get("delay_multiplier", 1.0),
                "road_closure": conditions.get("road_closure", False),
                "objective_score": round(objective, 4),
                "conditions": conditions,
            }
        )

    ranked.sort(key=lambda item: item["objective_score"])
    best = ranked[0] if ranked else None
    current = ranked[0] if ranked and ranked[0]["id"] == "current" else next((r for r in ranked if r["id"] == "current"), None)
    return {
        "truck_id": str(truck.get("id", "")),
        "callsign": truck.get("callsign", truck.get("id", "")),
        "lane_id": truck.get("lane_id"),
        "mode": mode,
        "disruption": _disruption_summary(disruption),
        "best_route": best,
        "current_route": current,
        "options": ranked,
        "decision": _decision_text(best, ranked, disruption),
    }


def predict_delay(truck: Mapping[str, Any], risk: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Predict delay using route conditions, service level, cargo, and risk context."""
    optimization = optimize_route_for_truck(truck)
    best = optimization.get("best_route") or {}
    condition_score = float(best.get("condition_score", 0.0))
    risk_score = float((risk or {}).get("risk_score", 0.0))
    service_level = str(truck.get("service_level", "standard"))
    cargo_priority = int(float(truck.get("cargo_priority", 1)))

    service_buffer = {"expedited": 0.85, "just_in_time": 1.25, "temperature_controlled": 1.15, "cold_chain": 1.1}.get(service_level, 1.0)
    priority_buffer = 1.0 + max(0, cargo_priority - 1) * 0.08
    predicted = float(best.get("delay_min", 0.0)) + condition_score * 80.0 + risk_score * 60.0
    predicted *= service_buffer * priority_buffer

    confidence = _clamp(0.72 + 0.18 * bool(best) - 0.15 * condition_score)
    label = "ON_TIME"
    if predicted >= 180:
        label = "SEVERE_DELAY"
    elif predicted >= 75:
        label = "DELAY_LIKELY"
    elif predicted >= 25:
        label = "WATCH"

    return {
        "truck_id": str(truck.get("id", "")),
        "callsign": truck.get("callsign", truck.get("id", "")),
        "predicted_delay_min": round(predicted, 1),
        "delay_label": label,
        "confidence": round(confidence, 2),
        "drivers": {
            "condition_score": round(condition_score, 3),
            "risk_score": round(risk_score, 3),
            "service_level": service_level,
            "cargo_priority": cargo_priority,
        },
        "recommended_route_id": best.get("id"),
    }


def build_risk_analysis(
    trucks: Iterable[Mapping[str, Any]],
    risks: Iterable[Mapping[str, Any]],
    hazards: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Create a portfolio-level supply-chain risk summary."""
    trucks_list = list(trucks)
    risks_list = list(risks)
    hazards_list = list(hazards)
    high_risk = [r for r in risks_list if r.get("risk_label") in ("HIGH", "CRITICAL")]
    total_cost = sum(float(t.get("projected_cost_usd", t.get("base_cost_usd", 0))) for t in trucks_list)
    at_risk_cost = sum(
        float(next((t for t in trucks_list if str(t.get("id")) == str(r.get("truck_id"))), {}).get("projected_cost_usd", 0))
        for r in high_risk
    )
    lanes_at_risk = sorted(
        {
            str(next((t for t in trucks_list if str(t.get("id")) == str(r.get("truck_id"))), {}).get("lane_id", "unknown"))
            for r in high_risk
        }
    )
    return {
        "fleet_size": len(trucks_list),
        "active_hazards": len(hazards_list),
        "high_risk_shipments": len(high_risk),
        "lanes_at_risk": lanes_at_risk,
        "total_network_value_usd": round(total_cost, 2),
        "value_at_risk_usd": round(at_risk_cost, 2),
        "risk_ratio": round(at_risk_cost / total_cost, 4) if total_cost else 0.0,
        "top_risks": sorted(high_risk, key=lambda item: float(item.get("risk_score", 0.0)), reverse=True)[:5],
    }


def _candidate_routes(
    base_points: List[Coordinate],
    mode: str,
    disruption: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    candidates = [
        {"id": "current", "label": "Current committed lane", "strategy": "baseline", "points": base_points},
    ]
    if len(base_points) >= 2:
        candidates.append(
            {
                "id": "resilient-detour",
                "label": "Weather-resilient detour",
                "strategy": "reroute",
                "points": _detour_points(base_points, disruption, offset=3.0 if mode != "road" else 0.55),
            }
        )
        candidates.append(
            {
                "id": "cost-optimized",
                "label": "Cost-optimized consolidation lane",
                "strategy": "cost",
                "points": _detour_points(base_points, disruption, offset=-2.0 if mode != "road" else -0.35),
            }
        )
    return candidates


def _detour_points(
    points: List[Coordinate],
    disruption: Optional[Mapping[str, Any]],
    offset: float,
) -> List[Coordinate]:
    start = points[0]
    end = points[-1]
    mid = points[len(points) // 2]
    if disruption and disruption.get("centroid_lat") is not None and disruption.get("centroid_lng") is not None:
        hazard_lat = float(disruption["centroid_lat"])
        hazard_lng = float(disruption["centroid_lng"])
        push_lat = offset if mid[0] <= hazard_lat else -offset
        push_lng = -offset if mid[1] <= hazard_lng else offset
    else:
        push_lat = offset
        push_lng = -offset / 2.0
    waypoint = (mid[0] + push_lat, mid[1] + push_lng)
    if len(points) <= 3:
        return [start, waypoint, end]
    return [start, points[1], waypoint, points[-2], end]


def _truck_route_points(truck: Mapping[str, Any], lane: Optional[Mapping[str, Any]]) -> List[Coordinate]:
    route = truck.get("route_polyline") or (lane or {}).get("points") or []
    points: List[Coordinate] = []
    for item in route:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append((float(item[0]), float(item[1])))
    if not points:
        points = [(float(truck.get("lat", 0.0)), float(truck.get("lng", 0.0)))]
    current = (float(truck.get("lat", points[0][0])), float(truck.get("lng", points[0][1])))
    if points[0] != current:
        points = [current] + points[1:]
    return points


def _estimate_cost(base_cost: float, distance_km: float, base_distance_km: float, route_risk: float, mode: str) -> float:
    if base_distance_km <= 0:
        base_distance_km = max(distance_km, 1.0)
    distance_ratio = distance_km / base_distance_km
    risk_premium = 1.0 + route_risk * (0.22 if mode == "road" else 0.16)
    return base_cost * (0.55 + 0.45 * distance_ratio) * risk_premium


def _objective_score(
    duration_min: float,
    planned_duration_min: float,
    cost_usd: float,
    base_cost_usd: float,
    route_risk: float,
    reliability: float,
) -> float:
    duration_ratio = duration_min / max(planned_duration_min, 1.0)
    cost_ratio = cost_usd / max(base_cost_usd, 1.0)
    reliability_penalty = 1.0 - _clamp(reliability)
    return 0.35 * duration_ratio + 0.25 * cost_ratio + 0.30 * route_risk + 0.10 * reliability_penalty


def _hazard_penalty(points: List[Coordinate], disruption: Optional[Mapping[str, Any]]) -> float:
    if not disruption:
        return 0.0
    try:
        hazard_lat = float(disruption.get("centroid_lat"))
        hazard_lng = float(disruption.get("centroid_lng"))
    except (TypeError, ValueError):
        return 0.0
    radius = float(disruption.get("radius_km", 50.0))
    min_distance = min(haversine_km(lat, lng, hazard_lat, hazard_lng) for lat, lng in points)
    if min_distance <= radius:
        return 0.45
    if min_distance <= radius + 100:
        return 0.22
    return 0.0


def _disruption_summary(disruption: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not disruption:
        return None
    return {
        "id": disruption.get("id"),
        "title": disruption.get("title"),
        "type": disruption.get("event_type") or disruption.get("type"),
        "severity_weight": disruption.get("severity_weight"),
        "centroid_lat": disruption.get("centroid_lat"),
        "centroid_lng": disruption.get("centroid_lng"),
    }


def _decision_text(
    best: Optional[Mapping[str, Any]],
    ranked: List[Mapping[str, Any]],
    disruption: Optional[Mapping[str, Any]],
) -> str:
    if not best:
        return "No viable route options were generated."
    baseline = next((item for item in ranked if item.get("id") == "current"), None)
    if disruption and best.get("id") != "current":
        return f"Use {best.get('label')} to reduce exposure to {disruption.get('title', 'the disruption')}."
    if baseline and best.get("id") != "current":
        savings = float(baseline.get("cost_usd", 0.0)) - float(best.get("cost_usd", 0.0))
        if savings > 0:
            return f"Use {best.get('label')} for an estimated ${savings:,.0f} cost improvement."
    return "Stay on the committed lane; it has the best weighted cost, risk, and duration score."


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))
