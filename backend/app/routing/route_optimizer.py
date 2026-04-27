"""Compatibility wrappers around the intelligent optimization engine."""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..services.condition_providers import get_osrm_route
from ..services.optimization_engine import optimize_route_for_truck

Coordinate = Tuple[float, float]


def get_route(start: tuple, end: tuple) -> list:
    """Get route coordinates from OSRM with deterministic fallback."""
    return get_osrm_route([(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))])["coordinates"]


def get_detour_route(truck: dict, hazard: dict) -> dict:
    """Compute the best detour using condition-aware route optimization."""
    optimization = optimize_route_for_truck(truck, hazard)
    best = optimization.get("best_route") or {}
    current = optimization.get("current_route") or {}
    extra_km = float(best.get("distance_km", 0.0)) - float(current.get("distance_km", 0.0))
    extra_min = float(best.get("duration_min", 0.0)) - float(current.get("duration_min", 0.0))
    return {
        "coordinates": best.get("coordinates", truck.get("route_polyline", [])),
        "distance_km": best.get("distance_km", 0.0),
        "duration_min": best.get("duration_min", 0.0),
        "extra_km": round(extra_km, 1),
        "extra_min": max(0, round(extra_min)),
        "cost_usd": best.get("cost_usd", 0.0),
        "risk_index": best.get("risk_index", 0.0),
        "condition_label": best.get("condition_label", "LOW"),
        "strategy": best.get("strategy", "baseline"),
        "decision": optimization.get("decision"),
        "options": optimization.get("options", []),
    }
