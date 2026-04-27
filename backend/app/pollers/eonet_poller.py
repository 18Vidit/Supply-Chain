"""NASA EONET (Earth Observatory Natural Event Tracker) poller."""

import logging
import os

import httpx

from .normalization import normalize_hazard

logger = logging.getLogger(__name__)

EONET_BASE_URL = os.getenv(
    "EONET_BASE_URL", "https://eonet.gsfc.nasa.gov/api/v3/events"
)


async def fetch_eonet_events() -> list[dict]:
    """Fetch active natural events from NASA EONET and return normalized hazard dicts."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(EONET_BASE_URL, params={"status": "open", "limit": 50})
            response.raise_for_status()
            data = response.json()

        events = data.get("events", [])
        hazards: list[dict] = []
        for event in events:
            try:
                hazards.append(normalize_hazard(event, "eonet"))
            except Exception:
                logger.exception("Failed to normalize EONET event %s", event.get("id"))
        logger.info("EONET: fetched %d events, normalized %d", len(events), len(hazards))
        return hazards
    except httpx.HTTPError:
        logger.exception("EONET API request failed")
        return []
