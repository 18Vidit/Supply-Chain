"""National Weather Service Alerts poller."""

import logging
import os

import httpx

from .normalization import normalize_hazard

logger = logging.getLogger(__name__)

NWS_BASE_URL = os.getenv(
    "NWS_BASE_URL", "https://api.weather.gov/alerts/active"
)

_HEADERS = {"User-Agent": "HazardTracker/1.0 (contact@example.com)", "Accept": "application/geo+json"}


async def fetch_nws_alerts() -> list[dict]:
    """Fetch active NWS alerts and return normalized hazard dicts."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(NWS_BASE_URL, headers=_HEADERS)
            response.raise_for_status()
            data = response.json()

        features = data.get("features", [])
        hazards: list[dict] = []
        for feature in features:
            try:
                hazards.append(normalize_hazard(feature, "nws"))
            except Exception:
                logger.exception("Failed to normalize NWS alert %s", feature.get("id"))
        logger.info("NWS: fetched %d alerts, normalized %d", len(features), len(hazards))
        return hazards
    except httpx.HTTPError:
        logger.exception("NWS API request failed")
        return []
