"""Data-driven in-memory fleet movement for the global demo."""

from __future__ import annotations

import copy
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

from ..domain.global_network import Coordinate, build_fleet_seed, haversine_km

trucks: List[Dict[str, Any]] = []

_lock = threading.RLock()
_thread: threading.Thread | None = None
_stop_event = threading.Event()
_sim_speed = 1.5
_simulation_tick_seconds = 0.45


def load_trucks() -> List[Dict[str, Any]]:
    """Compatibility helper for older code paths."""
    with _lock:
        _ensure_trucks()
        return get_trucks()


def start_simulation() -> None:
    """Start the background movement loop once."""
    global _thread

    with _lock:
        _ensure_trucks()
        if _thread and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(target=_simulation_loop, name="global-fleet-movement", daemon=True)
        _thread.start()


def stop_simulation() -> None:
    """Stop the background movement loop."""
    _stop_event.set()


def set_simulation_speed(multiplier: float) -> float:
    """Set the movement speed multiplier and return the applied value."""
    global _sim_speed
    with _lock:
        _sim_speed = max(0.1, min(float(multiplier), 20.0))
        return _sim_speed


def get_trucks() -> List[Dict[str, Any]]:
    """Return public shipment state for API consumers."""
    with _lock:
        _ensure_trucks()
        return [_public_truck(t) for t in trucks]


def reroute_truck(truck_id: str, new_lat: float, new_lng: float, route_plan: Dict[str, Any] | None = None) -> bool:
    """Send a shipment toward a new temporary destination or optimized route."""
    with _lock:
        _ensure_trucks()
        target = str(truck_id)
        for truck in trucks:
            if str(truck.get("id")) == target or truck.get("callsign") == target:
                current = (float(truck["lat"]), float(truck["lng"]))
                if route_plan and route_plan.get("coordinates"):
                    points = [(float(lat), float(lng)) for lat, lng in route_plan["coordinates"]]
                    if points and points[0] != current:
                        points = [current] + points[1:]
                    route_name = route_plan.get("label", f"Optimized route for {truck['callsign']}")
                    truck["projected_cost_usd"] = route_plan.get("cost_usd", truck.get("projected_cost_usd"))
                    truck["condition_risk_index"] = route_plan.get("risk_index", truck.get("condition_risk_index", 0.0))
                else:
                    waypoint = ((current[0] + new_lat) / 2 + 0.18, (current[1] + new_lng) / 2 - 0.18)
                    points = [current, waypoint, (float(new_lat), float(new_lng))]
                    route_name = f"Manual reroute for {truck['callsign']}"

                route = {
                    "route_name": route_name,
                    "origin": truck.get("origin", "Current position"),
                    "destination": "Optimized safe waypoint",
                    "points": points,
                }
                truck["_route"] = route
                truck["_route_index"] = 0
                truck["_segment_progress_km"] = 0.0
                truck["route_name"] = route["route_name"]
                truck["destination"] = route["destination"]
                truck["route_polyline"] = [[lat, lng] for lat, lng in route["points"]]
                truck["status"] = "REROUTED"
                truck["last_updated"] = _utc_now()
                return True
    return False


def _simulation_loop() -> None:
    last_tick = time.time()
    while not _stop_event.is_set():
        now = time.time()
        elapsed = max(0.05, now - last_tick)
        last_tick = now
        with _lock:
            for truck in trucks:
                _advance_truck(truck, elapsed * _sim_speed)
        _stop_event.wait(_simulation_tick_seconds)


def _ensure_trucks() -> None:
    if trucks:
        return
    trucks.extend(build_fleet_seed(100))
    for idx, truck in enumerate(trucks):
        _advance_truck(truck, (idx % 18) * 480)


def _advance_truck(truck: Dict[str, Any], seconds: float) -> None:
    route = truck["_route"]
    points: List[Coordinate] = route["points"]
    if len(points) < 2 or truck.get("base_speed_kmh", truck.get("speed_kmh", 0)) <= 0:
        truck["last_updated"] = _utc_now()
        return

    effective_speed = float(truck.get("base_speed_kmh", truck.get("speed_kmh", 1.0))) * float(truck.get("_condition_speed_factor", 1.0))
    effective_speed = max(5.0, effective_speed)
    truck["speed_kmh"] = round(effective_speed, 1)
    truck["speed"] = round(effective_speed, 1)
    remaining_km = effective_speed * seconds / 3600.0

    while remaining_km > 0:
        idx = int(truck["_route_index"])
        next_idx = min(idx + 1, len(points) - 1)
        start = points[idx]
        end = points[next_idx]
        segment_km = max(haversine_km(start[0], start[1], end[0], end[1]), 0.1)
        progress = float(truck["_segment_progress_km"])
        available = segment_km - progress

        if remaining_km < available:
            truck["_segment_progress_km"] = progress + remaining_km
            break

        remaining_km -= available
        if next_idx >= len(points) - 1:
            truck["_route_index"] = 0
            truck["_segment_progress_km"] = 0.0
            if truck.get("status") == "REROUTED":
                truck["status"] = "ON_ROUTE"
        else:
            truck["_route_index"] = next_idx
            truck["_segment_progress_km"] = 0.0

    idx = int(truck["_route_index"])
    next_idx = min(idx + 1, len(points) - 1)
    start = points[idx]
    end = points[next_idx]
    segment_km = max(haversine_km(start[0], start[1], end[0], end[1]), 0.1)
    ratio = max(0.0, min(1.0, float(truck["_segment_progress_km"]) / segment_km))
    lat, lng = _interpolate(start, end, ratio)

    truck["lat"] = round(lat, 5)
    truck["lng"] = round(lng, 5)
    truck["heading_deg"] = round(_bearing_degrees(start[0], start[1], end[0], end[1]), 1)
    truck["eta"] = _eta_for_remaining(points, idx, truck["_segment_progress_km"], effective_speed, truck.get("customs_buffer_hours", 0.0)).isoformat()
    truck["last_updated"] = _utc_now()


def _public_truck(truck: Dict[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in truck.items() if not key.startswith("_")}


def _eta_for_remaining(
    points: List[Coordinate],
    idx: int,
    progress_km: float,
    speed_kmh: float,
    customs_buffer_hours: float,
) -> datetime:
    if len(points) < 2:
        return datetime.now(timezone.utc)
    next_idx = min(idx + 1, len(points) - 1)
    current_segment = haversine_km(points[idx][0], points[idx][1], points[next_idx][0], points[next_idx][1])
    remaining = max(0.0, current_segment - progress_km)
    for a, b in zip(points[next_idx:], points[next_idx + 1:]):
        remaining += haversine_km(a[0], a[1], b[0], b[1])
    hours = remaining / max(speed_kmh, 1) + float(customs_buffer_hours or 0.0)
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _interpolate(start: Coordinate, end: Coordinate, ratio: float) -> Coordinate:
    return (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)


def _bearing_degrees(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lng = math.radians(lng2 - lng1)
    y = math.sin(delta_lng) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lng)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
