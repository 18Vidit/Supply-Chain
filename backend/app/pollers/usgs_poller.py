"""USGS Earthquake Hazards Program poller."""

import logging
import os

import httpx

from .normalization import normalize_hazard

logger = logging.getLogger(__name__)

USGS_BASE_URL = os.getenv(
    "USGS_BASE_URL",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
)


async def fetch_usgs_earthquakes() -> list[dict]:
    """Fetch recent M2.5+ earthquakes from USGS and return normalized hazard dicts."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(USGS_BASE_URL)
            response.raise_for_status()
            data = response.json()

        features = data.get("features", [])
        hazards: list[dict] = []
        for feature in features:
            try:
                hazards.append(normalize_hazard(feature, "usgs"))
            except Exception:
                logger.exception("Failed to normalize USGS quake %s", feature.get("id"))
        logger.info("USGS: fetched %d quakes, normalized %d", len(features), len(hazards))
        return hazards
    except httpx.HTTPError:
        logger.exception("USGS API request failed")
        return []
