"""
Configuration constants for the Risk Engine + Intelligence Layer.
All thresholds, weights, and API settings in one place.
"""

import os
from typing import Dict


# ═══════════════════════════════════════════════════════════════
# Risk Score Thresholds
# ═══════════════════════════════════════════════════════════════
HIGH_RISK_THRESHOLD: float = 0.65
CRITICAL_RISK_THRESHOLD: float = 0.82

# ═══════════════════════════════════════════════════════════════
# Risk Score Component Weights (must sum to 1.0)
# ═══════════════════════════════════════════════════════════════
WEIGHT_PROXIMITY: float = 0.35
WEIGHT_VELOCITY: float = 0.30
WEIGHT_SEVERITY: float = 0.25
WEIGHT_AQI: float = 0.10

# ═══════════════════════════════════════════════════════════════
# Spatial Detection Parameters
# ═══════════════════════════════════════════════════════════════
MAX_DETECTION_RADIUS_KM: float = 150.0
TIME_THRESHOLD_HOURS: float = 4.0
TRAJECTORY_PROJECTION_HOURS: float = 2.0
DEGREES_TO_KM_APPROX: float = 111.0  # 1 degree latitude ≈ 111km

# ═══════════════════════════════════════════════════════════════
# Cargo Priority Multipliers
# Priority 1 = standard, 2 = important, 3 = critical (medical)
# ═══════════════════════════════════════════════════════════════
CARGO_PRIORITY_MULTIPLIER: Dict[int, float] = {
    1: 1.0,
    2: 1.15,
    3: 1.30,
}

# ═══════════════════════════════════════════════════════════════
# Hazard Severity Weights (hardcoded reference table)
# ═══════════════════════════════════════════════════════════════
SEVERITY_WEIGHTS: Dict[str, float] = {
    "wildfire":             1.0,
    "tornado_warning":      1.0,
    "major_earthquake":     0.95,
    "flash_flood":          0.90,
    "severe_storm":         0.80,
    "winter_storm":         0.75,
    "moderate_earthquake":  0.70,
    "high_wind":            0.60,
    "flood_watch":          0.55,
    "dense_fog":             0.50,
}


# ═══════════════════════════════════════════════════════════════
# Weather Forecast Thresholds (Open-Meteo)
# ═══════════════════════════════════════════════════════════════
FORECAST_THRESHOLDS = {
    "high_wind": {
        "field": "wind_speed_10m",
        "threshold": 80.0,       # km/h
        "unit": "km/h",
    },
    "heavy_precipitation": {
        "field": "precipitation",
        "threshold": 20.0,       # mm/hr
        "unit": "mm/hr",
    },
    "heavy_snow": {
        "field": "snowfall",
        "threshold": 5.0,        # cm/hr
        "unit": "cm/hr",
    },
    "zero_visibility": {
        "field": "visibility",
        "threshold": 1000.0,     # meters (below = dangerous)
        "unit": "m",
        "below": True,           # alert when BELOW threshold
    },
}

# ═══════════════════════════════════════════════════════════════
# Engine Scheduler Intervals
# ═══════════════════════════════════════════════════════════════
RISK_ENGINE_INTERVAL_SEC: int = 30
FORECAST_CHECK_INTERVAL_SEC: int = 600  # 10 minutes


OPEN_METEO_BASE_URL: str = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PARAMS: Dict[str, str] = {
    "hourly": "temperature_2m,wind_speed_10m,precipitation,snowfall,visibility",
    "forecast_days": "1",
}

AIRNOW_API_KEY: str = os.getenv("AIRNOW_API_KEY", "")
AIRNOW_BASE_URL: str = "https://www.airnowapi.org/aq/observation/latLong/current/"
AQI_CACHE_TTL_SEC: int = int(os.getenv("AQI_CACHE_TTL_SEC", "300"))

# ═══════════════════════════════════════════════════════════════
# Firebase paths (interface with Person 3)
# ═══════════════════════════════════════════════════════════════
FIREBASE_ALERTS_PATH: str = "/alerts"
FIREBASE_RISK_EVENTS_PATH: str = "/risk_events"
