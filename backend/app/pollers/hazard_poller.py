"""
Hazard Poller — generates RANDOM hazard zones every call (heat zones / hazardous areas).
Also fetches live data from NASA EONET, USGS, Open-Meteo.
"""

import requests
import random
import math
from datetime import datetime, timezone

SEVERITY_WEIGHTS = {
    "wildfire":              1.00,
    "tornado_warning":       1.00,
    "major_earthquake":      0.95,
    "flash_flood":           0.90,
    "severe_storm":          0.85,
    "winter_storm":          0.75,
    "moderate_earthquake":   0.70,
    "high_wind":             0.60,
    "flood_watch":           0.55,
    "dense_fog":             0.50,
    "unknown":               0.40,
}

# Hazard type pool for random generation
HAZARD_TYPES = [
    ("wildfire",        "Industrial Fire"),
    ("flash_flood",     "Flash Flood Warning"),
    ("dense_fog",       "Dense Fog Advisory"),
    ("high_wind",       "Dust Storm / High Wind"),
    ("severe_storm",    "Severe Storm Warning"),
    ("tornado_warning", "Tornado Warning"),
    ("flood_watch",     "Flood Watch"),
]

# Indian region anchors: (lat, lng, region_name)
INDIA_REGIONS = [
    (28.45, 77.03, "Gurgaon Corridor"),
    (27.15, 80.50, "NH-44 Agra–Lucknow"),
    (26.00, 72.75, "Rajasthan Desert"),
    (22.50, 71.65, "Gujarat Coastal Belt"),
    (17.50, 73.50, "Maharashtra Coast"),
    (22.00, 78.50, "Madhya Pradesh Forest"),
    (16.25, 79.50, "Krishna River Basin"),
    (13.00, 78.50, "Bengaluru–Chennai NH-48"),
    (19.25, 86.00, "Bay of Bengal Coast"),
    (26.50, 91.25, "Brahmaputra Valley"),
    (31.00, 75.50, "Punjab Plains"),
    (10.50, 76.75, "Kerala Western Ghats"),
    (25.30, 83.00, "Varanasi–Allahabad Belt"),
    (21.50, 86.50, "Odisha Coastal Zone"),
    (23.50, 85.20, "Jharkhand Mining Belt"),
    (15.00, 76.50, "Karnataka Deccan Plateau"),
    (30.00, 78.00, "Uttarakhand Mountain Pass"),
    (11.00, 77.00, "Tamil Nadu Coimbatore Zone"),
    (24.00, 88.00, "West Bengal Border"),
    (20.00, 73.80, "Nashik–Trimbak Region"),
]


def _make_circle_poly(lat, lng, radius_deg=0.8):
    coords = [
        [lng + radius_deg * math.cos(math.radians(a)),
         lat + radius_deg * math.sin(math.radians(a))]
        for a in range(0, 360, 20)
    ]
    coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def _make_rect_poly(lat_min, lng_min, lat_max, lng_max):
    return {
        "type": "Polygon",
        "coordinates": [[
            [lng_min, lat_min], [lng_max, lat_min],
            [lng_max, lat_max], [lng_min, lat_max],
            [lng_min, lat_min]
        ]]
    }


def _generate_random_static_hazards():
    """
    Generate 12–15 RANDOM hazard zones across India on every call.
    Positions, types, and sizes vary each time (true randomness).
    """
    now = datetime.now(timezone.utc).isoformat()

    # Shuffle regions and pick 12–15
    regions = INDIA_REGIONS.copy()
    random.shuffle(regions)
    count = random.randint(12, 15)
    selected = regions[:count]

    hazards = []
    for idx, (base_lat, base_lng, region_name) in enumerate(selected):
        # Randomise position within ±1.5° of the anchor
        lat = base_lat + random.uniform(-1.2, 1.2)
        lng = base_lng + random.uniform(-1.2, 1.2)

        htype, hlabel = random.choice(HAZARD_TYPES)

        # Random shape: circle or rectangle
        if random.random() < 0.55:
            radius = random.uniform(0.4, 1.4)
            geometry = _make_circle_poly(lat, lng, radius)
            radius_km = radius * 111.0
        else:
            half_w = random.uniform(0.5, 2.0)
            half_h = random.uniform(0.5, 1.5)
            geometry = _make_rect_poly(lat - half_h, lng - half_w,
                                       lat + half_h, lng + half_w)
            radius_km = math.sqrt(half_w ** 2 + half_h ** 2) * 111.0

        # Slight severity randomisation around the base weight
        base_sev = SEVERITY_WEIGHTS.get(htype, 0.5)
        severity = round(min(1.0, base_sev + random.uniform(-0.1, 0.1)), 2)

        hazards.append({
            "id":              f"static-{idx+1:03d}",
            "source":          "simulated",
            "type":            htype,
            "title":           f"{hlabel} — {region_name}",
            "severity_weight": severity,
            "geometry":        geometry,
            "centroid_lat":    round(lat, 4),
            "centroid_lng":    round(lng, 4),
            "radius_km":       round(radius_km, 1),
            "fetched_at":      now,
        })

    return hazards


# ────────────────────────────────────────────────────────────
# Live API fetchers
# ────────────────────────────────────────────────────────────

def _fetch_eonet():
    try:
        res = requests.get(
            "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=20",
            timeout=6
        ).json()
        hazards = []
        for ev in res.get("events", []):
            geom = ev.get("geometry", [{}])
            if not geom:
                continue
            g0     = geom[0]
            coords = g0.get("coordinates", [])
            if not coords:
                continue
            cat   = ev.get("categories", [{}])[0].get("id", "")
            etype = ("wildfire"    if "wildfire" in cat.lower() else
                     "severe_storm" if "storm"   in cat.lower() else
                     "flash_flood"  if "flood"   in cat.lower() else "unknown")
            if g0["type"] == "Point":
                lng0, lat0 = coords
                r = 0.5
                poly_coords = [
                    [lng0 + r * math.cos(math.radians(a)),
                     lat0 + r * math.sin(math.radians(a))]
                    for a in range(0, 360, 20)
                ]
                poly_coords.append(poly_coords[0])
                geometry    = {"type": "Polygon", "coordinates": [poly_coords]}
                clat, clng  = lat0, lng0
            else:
                geometry = {"type": "Polygon", "coordinates": [coords]}
                clat = sum(c[1] for c in coords) / len(coords)
                clng = sum(c[0] for c in coords) / len(coords)

            hazards.append({
                "id":              f"eonet-{ev['id']}",
                "source":          "eonet",
                "type":            etype,
                "title":           ev.get("title", "EONET Event"),
                "severity_weight": SEVERITY_WEIGHTS.get(etype, 0.5),
                "geometry":        geometry,
                "centroid_lat":    clat,
                "centroid_lng":    clng,
                "radius_km":       50.0,
                "fetched_at":      datetime.now(timezone.utc).isoformat(),
            })
        return hazards
    except Exception:
        return []


def _fetch_usgs():
    try:
        res = requests.get(
            "https://earthquake.usgs.gov/fdsnws/event/1/query"
            "?format=geojson&minmagnitude=4.0&limit=15&orderby=time",
            timeout=6
        ).json()
        hazards = []
        for feat in res.get("features", []):
            props  = feat.get("properties", {})
            mag    = props.get("mag", 4.0)
            coords = feat["geometry"]["coordinates"]
            lng0, lat0 = coords[0], coords[1]
            etype  = "major_earthquake" if mag >= 6.0 else "moderate_earthquake"
            r = 0.4
            poly_coords = [
                [lng0 + r * math.cos(math.radians(a)),
                 lat0 + r * math.sin(math.radians(a))]
                for a in range(0, 360, 20)
            ]
            poly_coords.append(poly_coords[0])
            hazards.append({
                "id":              f"usgs-{feat['id']}",
                "source":          "usgs",
                "type":            etype,
                "title":           f"M{mag} Earthquake — {props.get('place', 'Unknown')}",
                "severity_weight": SEVERITY_WEIGHTS.get(etype, 0.7),
                "geometry":        {"type": "Polygon", "coordinates": [poly_coords]},
                "centroid_lat":    lat0,
                "centroid_lng":    lng0,
                "radius_km":       40.0,
                "fetched_at":      datetime.now(timezone.utc).isoformat(),
            })
        return hazards
    except Exception:
        return []


def _fetch_openmeteo():
    locations = [
        (28.63, 77.22, "Delhi"),   (19.07, 72.87, "Mumbai"),
        (12.97, 77.59, "Bengaluru"), (22.57, 88.36, "Kolkata"),
    ]
    hazards = []
    for lat, lng, name in locations:
        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lng}"
                f"&hourly=windspeed_10m,precipitation,visibility"
                f"&forecast_days=1"
            )
            res    = requests.get(url, timeout=5).json()
            hourly = res.get("hourly", {})
            winds  = hourly.get("windspeed_10m", [])
            precip = hourly.get("precipitation", [])
            vis    = hourly.get("visibility", [])

            max_wind   = max(winds)  if winds  else 0
            max_precip = max(precip) if precip else 0
            min_vis    = min(vis)    if vis    else 9999
            r = 0.6

            if max_wind > 60:
                hazards.append({
                    "id":              f"forecast-wind-{name}",
                    "source":          "openmeteo",
                    "type":            "high_wind",
                    "title":           f"High Wind Forecast — {name} {max_wind:.0f} km/h",
                    "severity_weight": 0.60,
                    "geometry":        _make_circle_poly(lat, lng, r),
                    "centroid_lat":    lat,
                    "centroid_lng":    lng,
                    "radius_km":       65.0,
                    "fetched_at":      datetime.now(timezone.utc).isoformat(),
                })
            if max_precip > 20:
                hazards.append({
                    "id":              f"forecast-rain-{name}",
                    "source":          "openmeteo",
                    "type":            "flash_flood",
                    "title":           f"Heavy Rain — {name} {max_precip:.0f} mm/hr",
                    "severity_weight": 0.88,
                    "geometry":        _make_circle_poly(lat, lng, r),
                    "centroid_lat":    lat,
                    "centroid_lng":    lng,
                    "radius_km":       65.0,
                    "fetched_at":      datetime.now(timezone.utc).isoformat(),
                })
            if min_vis < 1000:
                hazards.append({
                    "id":              f"forecast-fog-{name}",
                    "source":          "openmeteo",
                    "type":            "dense_fog",
                    "title":           f"Low Visibility — {name} {min_vis:.0f} m",
                    "severity_weight": 0.50,
                    "geometry":        _make_circle_poly(lat, lng, r),
                    "centroid_lat":    lat,
                    "centroid_lng":    lng,
                    "radius_km":       65.0,
                    "fetched_at":      datetime.now(timezone.utc).isoformat(),
                })
        except Exception:
            pass
    return hazards


# ────────────────────────────────────────────────────────────
# Cache — hazards persist for CACHE_TTL seconds so the map is stable
# ────────────────────────────────────────────────────────────
import time as _time

_hazard_cache: list = []
_cache_ts: float = 0.0
CACHE_TTL = 60  # seconds

def get_all_hazards():
    """Returns cached hazard zones (simulated + live). Refreshes every 60s."""
    global _hazard_cache, _cache_ts

    now = _time.time()
    if _hazard_cache and (now - _cache_ts) < CACHE_TTL:
        return _hazard_cache

    simulated = _generate_random_static_hazards()
    live = []
    live += _fetch_eonet()
    live += _fetch_usgs()
    live += _fetch_openmeteo()

    _hazard_cache = simulated + live
    _cache_ts = now
    return _hazard_cache
