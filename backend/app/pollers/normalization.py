"""Normalization layer — converts raw API payloads into a unified hazard dict."""

from typing import Any

# ---------------------------------------------------------------------------
# Severity mapping dictionaries (source-specific → 0.0 – 1.0)
# ---------------------------------------------------------------------------

EONET_SEVERITY: dict[str, float] = {
    "wildfires": 0.9,
    "severeStorms": 0.85,
    "volcanoes": 0.95,
    "floods": 0.8,
    "earthquakes": 0.9,
    "drought": 0.5,
    "dustHaze": 0.3,
    "landslides": 0.75,
    "seaLakeIce": 0.4,
    "snow": 0.4,
    "tempExtremes": 0.6,
    "waterColor": 0.2,
    "manmade": 0.7,
}

NWS_SEVERITY: dict[str, float] = {
    "Extreme": 1.0,
    "Severe": 0.85,
    "Moderate": 0.6,
    "Minor": 0.35,
    "Unknown": 0.3,
}

USGS_MAGNITUDE_THRESHOLDS: list[tuple[float, float]] = [
    (7.0, 1.0),
    (6.0, 0.9),
    (5.0, 0.75),
    (4.0, 0.55),
    (3.0, 0.35),
    (0.0, 0.2),
]

FEMA_SEVERITY: dict[str, float] = {
    "Fire": 0.9,
    "Hurricane": 0.95,
    "Flood": 0.8,
    "Tornado": 0.9,
    "Earthquake": 0.9,
    "Severe Storm": 0.85,
    "Snow": 0.4,
    "Drought": 0.5,
    "Typhoon": 0.95,
    "Coastal Storm": 0.7,
    "Mud/Landslide": 0.75,
    "Volcanic Eruption": 0.95,
    "Tsunami": 1.0,
}


def _usgs_severity(magnitude: float) -> float:
    for threshold, weight in USGS_MAGNITUDE_THRESHOLDS:
        if magnitude >= threshold:
            return weight
    return 0.2


def _centroid_from_polygon(coords: list) -> tuple[float, float]:
    """Compute a simple average centroid from a GeoJSON polygon coordinate ring."""
    flat: list[tuple[float, float]] = []
    if coords and isinstance(coords[0], list) and isinstance(coords[0][0], list):
        flat = [(p[0], p[1]) for p in coords[0]]
    elif coords and isinstance(coords[0], (int, float)):
        return coords[1], coords[0]
    else:
        flat = [(p[0], p[1]) for p in coords]

    if not flat:
        return 0.0, 0.0
    avg_lng = sum(p[0] for p in flat) / len(flat)
    avg_lat = sum(p[1] for p in flat) / len(flat)
    return avg_lat, avg_lng


def normalize_hazard(raw_data: dict[str, Any], source: str) -> dict[str, Any]:
    """Normalize a raw hazard payload into a unified dict for upsert."""

    if source == "eonet":
        geometry = raw_data.get("geometry", [{}])
        first_geo = geometry[0] if geometry else {}
        coords = first_geo.get("coordinates", [0, 0])
        geo_type = first_geo.get("type", "Point")

        if geo_type == "Point":
            lat, lng = coords[1], coords[0]
            geojson = {"type": "Point", "coordinates": coords}
        else:
            lat, lng = _centroid_from_polygon(coords)
            geojson = {"type": geo_type, "coordinates": coords}

        categories = raw_data.get("categories", [])
        cat_id = categories[0].get("id", "unknown") if categories else "unknown"

        return {
            "external_id": raw_data.get("id") or "unknown",
            "source_api": "eonet",
            "event_type": cat_id,
            "title": raw_data.get("title") or "EONET Event",
            "geometry_geojson": geojson,
            "centroid_lat": lat,
            "centroid_lng": lng,
            "severity_weight": EONET_SEVERITY.get(cat_id, 0.5),
        }

    if source == "nws":
        props = raw_data.get("properties", {}) or {}
        geometry = raw_data.get("geometry") or {"type": "Point", "coordinates": [0, 0]}

        if geometry["type"] == "Point":
            coords = geometry.get("coordinates", [0, 0])
            lat, lng = coords[1], coords[0]
        else:
            lat, lng = _centroid_from_polygon(geometry.get("coordinates", []))

        event_type = props.get("event") or "Weather Alert"
        title = props.get("headline") or props.get("event") or "NWS Alert"

        return {
            "external_id": props.get("id") or raw_data.get("id") or "unknown",
            "source_api": "nws",
            "event_type": event_type,
            "title": title,
            "geometry_geojson": geometry,
            "centroid_lat": lat,
            "centroid_lng": lng,
            "severity_weight": NWS_SEVERITY.get(props.get("severity") or "Unknown", 0.3),
        }

    if source == "usgs":
        props = raw_data.get("properties", {}) or {}
        geometry = raw_data.get("geometry") or {"type": "Point", "coordinates": [0, 0, 0]}
        coords = geometry.get("coordinates", [0, 0, 0])
        magnitude = props.get("mag") or 0.0

        return {
            "external_id": raw_data.get("id") or "unknown",
            "source_api": "usgs",
            "event_type": "earthquake",
            "title": props.get("title") or f"M{magnitude} Earthquake",
            "geometry_geojson": {"type": "Point", "coordinates": coords[:2]},
            "centroid_lat": coords[1],
            "centroid_lng": coords[0],
            "severity_weight": _usgs_severity(magnitude),
        }

    if source == "fema":
        incident_type = raw_data.get("incidentType") or "Unknown"
        return {
            "external_id": str(raw_data.get("disasterNumber") or raw_data.get("id") or "unknown"),
            "source_api": "fema",
            "event_type": incident_type,
            "title": raw_data.get("declarationTitle") or f"FEMA {incident_type}",
            "geometry_geojson": {"type": "Point", "coordinates": [0, 0]},
            "centroid_lat": 0.0,
            "centroid_lng": 0.0,
            "severity_weight": FEMA_SEVERITY.get(incident_type, 0.5),
        }

    raise ValueError(f"Unsupported source: {source}")
