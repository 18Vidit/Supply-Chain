"""FEMA Disaster Declarations poller."""

import logging
import os

import httpx

from .normalization import normalize_hazard

logger = logging.getLogger(__name__)

FEMA_BASE_URL = os.getenv(
    "FEMA_BASE_URL",
    "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries",
)


async def fetch_fema_disasters() -> list[dict]:
    """Fetch recent FEMA disaster declarations and return normalized hazard dicts."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                FEMA_BASE_URL,
                params={"$orderby": "declarationDate desc", "$top": 50},
            )
            response.raise_for_status()
            data = response.json()

        records = data.get("DisasterDeclarationsSummaries", [])
        hazards: list[dict] = []
        for record in records:
            try:
                hazards.append(normalize_hazard(record, "fema"))
            except Exception:
                logger.exception("Failed to normalize FEMA record %s", record.get("disasterNumber"))
        logger.info("FEMA: fetched %d records, normalized %d", len(records), len(hazards))
        return hazards
    except httpx.HTTPError:
        logger.exception("FEMA API request failed")
        return []
