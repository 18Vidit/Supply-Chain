"""Global supply-chain reference network.

The application is still lightweight enough to run as a local demo, but this
module gives it production-shaped primitives: countries, ports, lanes,
cross-border flows, service levels, costs, customs buffers, and route geometry.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

Coordinate = Tuple[float, float]


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    region: str
    risk_tier: str
    customs_complexity: float


@dataclass(frozen=True)
class Port:
    code: str
    name: str
    country_code: str
    city: str
    lat: float
    lng: float
    port_type: str
    annual_teu_m: float


@dataclass(frozen=True)
class LogisticsLane:
    id: str
    name: str
    mode: str
    origin: str
    destination: str
    origin_country: str
    destination_country: str
    flow_type: str
    incoterm: str
    service_level: str
    ports: Tuple[str, ...]
    points: Tuple[Coordinate, ...]
    base_cost_usd: float
    customs_buffer_hours: float
    carbon_kg: float
    base_speed_kmh: float
    reliability: float


COUNTRIES: Tuple[Country, ...] = (
    Country("IN", "India", "South Asia", "medium", 0.42),
    Country("SG", "Singapore", "Southeast Asia", "low", 0.16),
    Country("CN", "China", "East Asia", "medium", 0.34),
    Country("AE", "United Arab Emirates", "Middle East", "low", 0.22),
    Country("NL", "Netherlands", "Western Europe", "low", 0.18),
    Country("DE", "Germany", "Western Europe", "low", 0.20),
    Country("US", "United States", "North America", "medium", 0.30),
    Country("BR", "Brazil", "South America", "medium", 0.46),
    Country("ZA", "South Africa", "Africa", "medium", 0.44),
    Country("JP", "Japan", "East Asia", "low", 0.18),
)


PORTS: Tuple[Port, ...] = (
    Port("INNSA", "Nhava Sheva", "IN", "Mumbai", 18.9490, 72.9510, "seaport", 5.9),
    Port("INMAA", "Chennai Port", "IN", "Chennai", 13.0827, 80.2707, "seaport", 1.8),
    Port("SGSIN", "Port of Singapore", "SG", "Singapore", 1.2644, 103.8200, "transshipment", 37.3),
    Port("CNSHA", "Port of Shanghai", "CN", "Shanghai", 31.2304, 121.4737, "seaport", 47.0),
    Port("AEJEA", "Jebel Ali", "AE", "Dubai", 25.0118, 55.0613, "freezone", 14.0),
    Port("NLRTM", "Port of Rotterdam", "NL", "Rotterdam", 51.9490, 4.1450, "seaport", 14.5),
    Port("DEHAM", "Port of Hamburg", "DE", "Hamburg", 53.5511, 9.9937, "seaport", 8.3),
    Port("USLAX", "Port of Los Angeles", "US", "Los Angeles", 33.7405, -118.2775, "seaport", 9.9),
    Port("USNYC", "Port Newark", "US", "Newark", 40.6840, -74.1620, "seaport", 7.8),
    Port("BRSSZ", "Port of Santos", "BR", "Santos", -23.9608, -46.3336, "seaport", 4.2),
    Port("ZADUR", "Port of Durban", "ZA", "Durban", -29.8833, 31.0500, "seaport", 2.9),
    Port("JPTYO", "Tokyo Bay", "JP", "Tokyo", 35.5494, 139.7798, "seaport", 5.0),
)


LANES: Tuple[LogisticsLane, ...] = (
    LogisticsLane(
        id="lane-in-eu-pharma",
        name="India Pharma Export: Delhi ICD to Rotterdam Life Sciences",
        mode="intermodal",
        origin="Delhi ICD",
        destination="Rotterdam Life Sciences DC",
        origin_country="IN",
        destination_country="NL",
        flow_type="export",
        incoterm="CIF",
        service_level="temperature_controlled",
        ports=("INNSA", "AEJEA", "NLRTM"),
        points=((28.6139, 77.2090), (23.0225, 72.5714), (18.9490, 72.9510), (25.0118, 55.0613), (35.0, 20.0), (51.9490, 4.1450), (51.9244, 4.4777)),
        base_cost_usd=18200,
        customs_buffer_hours=28,
        carbon_kg=8200,
        base_speed_kmh=62,
        reliability=0.88,
    ),
    LogisticsLane(
        id="lane-cn-eu-electronics",
        name="China Electronics Export: Shanghai to Hamburg",
        mode="ocean",
        origin="Shanghai Export Zone",
        destination="Hamburg Consumer DC",
        origin_country="CN",
        destination_country="DE",
        flow_type="export",
        incoterm="FOB",
        service_level="standard",
        ports=("CNSHA", "SGSIN", "AEJEA", "DEHAM"),
        points=((31.2304, 121.4737), (22.3193, 114.1694), (1.2644, 103.8200), (8.0, 76.0), (25.0118, 55.0613), (36.0, 18.0), (53.5511, 9.9937)),
        base_cost_usd=24100,
        customs_buffer_hours=36,
        carbon_kg=14200,
        base_speed_kmh=38,
        reliability=0.82,
    ),
    LogisticsLane(
        id="lane-us-domestic-retail",
        name="US Retail Replenishment: Los Angeles to New York",
        mode="road",
        origin="Los Angeles Import DC",
        destination="Newark Retail DC",
        origin_country="US",
        destination_country="US",
        flow_type="domestic",
        incoterm="DAP",
        service_level="expedited",
        ports=("USLAX", "USNYC"),
        points=((33.7405, -118.2775), (34.0522, -118.2437), (36.1699, -115.1398), (39.7392, -104.9903), (41.8781, -87.6298), (40.7128, -74.0060), (40.6840, -74.1620)),
        base_cost_usd=9800,
        customs_buffer_hours=0,
        carbon_kg=6900,
        base_speed_kmh=74,
        reliability=0.86,
    ),
    LogisticsLane(
        id="lane-br-za-food",
        name="Brazil Food Export: Sao Paulo to Durban",
        mode="intermodal",
        origin="Sao Paulo Agro Hub",
        destination="Durban Cold Chain DC",
        origin_country="BR",
        destination_country="ZA",
        flow_type="export",
        incoterm="CFR",
        service_level="cold_chain",
        ports=("BRSSZ", "ZADUR"),
        points=((-23.5505, -46.6333), (-23.9608, -46.3336), (-28.0, -20.0), (-29.8833, 31.0500)),
        base_cost_usd=15600,
        customs_buffer_hours=34,
        carbon_kg=9700,
        base_speed_kmh=44,
        reliability=0.78,
    ),
    LogisticsLane(
        id="lane-in-apac-auto",
        name="India Auto Parts Export: Chennai to Tokyo",
        mode="ocean",
        origin="Chennai Auto Cluster",
        destination="Tokyo Bay OEM Plant",
        origin_country="IN",
        destination_country="JP",
        flow_type="export",
        incoterm="CIF",
        service_level="just_in_time",
        ports=("INMAA", "SGSIN", "JPTYO"),
        points=((12.9716, 77.5946), (13.0827, 80.2707), (1.2644, 103.8200), (21.0, 123.0), (35.5494, 139.7798)),
        base_cost_usd=13400,
        customs_buffer_hours=22,
        carbon_kg=7600,
        base_speed_kmh=42,
        reliability=0.84,
    ),
    LogisticsLane(
        id="lane-eu-me-machinery",
        name="Europe Machinery Export: Hamburg to Jebel Ali",
        mode="intermodal",
        origin="Hamburg Industrial Park",
        destination="Jebel Ali Free Zone",
        origin_country="DE",
        destination_country="AE",
        flow_type="export",
        incoterm="DAP",
        service_level="standard",
        ports=("DEHAM", "AEJEA"),
        points=((53.5511, 9.9937), (48.8566, 2.3522), (41.3851, 2.1734), (36.0, 18.0), (25.0118, 55.0613)),
        base_cost_usd=17600,
        customs_buffer_hours=24,
        carbon_kg=9200,
        base_speed_kmh=48,
        reliability=0.83,
    ),
)


def get_global_network_payload() -> Dict[str, Any]:
    """Return a JSON-serializable snapshot of the operating network."""
    countries = [asdict(country) for country in COUNTRIES]
    ports = [asdict(port) for port in PORTS]
    lanes = [_lane_to_dict(lane) for lane in LANES]
    return {
        "countries": countries,
        "ports": ports,
        "lanes": lanes,
        "stats": {
            "country_count": len(countries),
            "port_count": len(ports),
            "lane_count": len(lanes),
            "regions": sorted({country.region for country in COUNTRIES}),
            "total_base_cost_usd": round(sum(lane.base_cost_usd for lane in LANES), 2),
            "total_carbon_kg": round(sum(lane.carbon_kg for lane in LANES), 2),
        },
    }


def get_lane(lane_id: str) -> Optional[Dict[str, Any]]:
    """Find a lane by ID and return a defensive copy."""
    lane = next((lane for lane in LANES if lane.id == lane_id), None)
    return _lane_to_dict(lane) if lane else None


def build_fleet_seed(count: int = 100) -> List[Dict[str, Any]]:
    """Build deterministic fleet seed data from the global lane catalog."""
    cargo_cycle = (
        ("pharma", 3),
        ("electronics", 2),
        ("food", 2),
        ("industrial_parts", 1),
        ("automotive", 2),
        ("medical_devices", 3),
    )
    fleet: List[Dict[str, Any]] = []

    for idx in range(count):
        lane = LANES[idx % len(LANES)]
        cargo_type, priority = cargo_cycle[idx % len(cargo_cycle)]
        stagger = (idx % 10) / 10.0
        distance_km = route_distance_km(lane.points)
        planned_hours = distance_km / max(lane.base_speed_kmh, 1) + lane.customs_buffer_hours
        transport_category = mode_to_transport_category(lane.mode)
        fleet.append(
            {
                "id": f"trk-{idx + 1:03d}",
                "callsign": f"GLB-{idx + 1:03d}",
                "asset_type": "truck" if lane.mode == "road" else "container_unit",
                "cargo_type": cargo_type,
                "cargo_priority": priority,
                "status": "ON_ROUTE",
                "lane_id": lane.id,
                "route_name": lane.name,
                "origin": lane.origin,
                "destination": lane.destination,
                "origin_country": lane.origin_country,
                "destination_country": lane.destination_country,
                "flow_type": lane.flow_type,
                "incoterm": lane.incoterm,
                "service_level": lane.service_level,
                "mode": lane.mode,
                "transport_category": transport_category,
                "ports": list(lane.ports),
                "base_cost_usd": lane.base_cost_usd,
                "projected_cost_usd": lane.base_cost_usd,
                "customs_buffer_hours": lane.customs_buffer_hours,
                "carbon_kg": lane.carbon_kg,
                "reliability": lane.reliability,
                "planned_duration_hours": round(planned_hours, 1),
                "base_speed_kmh": lane.base_speed_kmh,
                "speed_kmh": lane.base_speed_kmh,
                "route_polyline": [[lat, lng] for lat, lng in lane.points],
                "_route": _lane_to_route(lane),
                "_route_index": min(int(stagger * max(len(lane.points) - 1, 1)), max(len(lane.points) - 2, 0)),
                "_segment_progress_km": 0.0,
                "_condition_speed_factor": 1.0,
            }
        )
    return fleet


def mode_to_transport_category(mode: str) -> str:
    value = str(mode or "").strip().lower()
    if value in {"road", "land"}:
        return "land"
    if value in {"ocean", "water", "sea"}:
        return "water"
    if value in {"air", "aerial", "intermodal"}:
        return "aerial"
    return "land"


def route_distance_km(points: Iterable[Coordinate]) -> float:
    """Calculate total haversine distance for a route polyline."""
    point_list = list(points)
    return round(
        sum(haversine_km(a[0], a[1], b[0], b[1]) for a, b in zip(point_list, point_list[1:])),
        2,
    )


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def port_lookup() -> Dict[str, Dict[str, Any]]:
    return {port.code: asdict(port) for port in PORTS}


def country_lookup() -> Dict[str, Dict[str, Any]]:
    return {country.code: asdict(country) for country in COUNTRIES}


def _lane_to_route(lane: LogisticsLane) -> Dict[str, Any]:
    return {
        "route_name": lane.name,
        "origin": lane.origin,
        "destination": lane.destination,
        "points": list(copy.deepcopy(lane.points)),
    }


def _lane_to_dict(lane: LogisticsLane) -> Dict[str, Any]:
    data = asdict(lane)
    data["points"] = [[lat, lng] for lat, lng in lane.points]
    data["distance_km"] = route_distance_km(lane.points)
    data["ports"] = list(lane.ports)
    return data
