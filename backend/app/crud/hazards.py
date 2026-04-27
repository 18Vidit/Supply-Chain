import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db_models import HazardZone
from ..database import async_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers (session injected — used by pollers and API routes)
# ---------------------------------------------------------------------------

async def upsert_hazard(session: AsyncSession, hazard: dict[str, Any]) -> HazardZone:
    """Insert or update a hazard by external_id."""
    stmt = select(HazardZone).where(HazardZone.external_id == hazard["external_id"])
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        for field, value in hazard.items():
            setattr(existing, field, value)
        existing.is_active = True
        existing.fetched_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(existing)
        return existing

    row = HazardZone(**hazard, is_active=True, fetched_at=datetime.now(timezone.utc))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def bulk_upsert_hazards(session: AsyncSession, hazards: list[dict[str, Any]]) -> int:
    """Upsert a batch of normalized hazard dicts. Returns count of persisted rows."""
    count = 0
    for h in hazards:
        try:
            await upsert_hazard(session, h)
            count += 1
        except Exception:
            logger.exception("Failed to upsert hazard %s", h.get("external_id"))
            await session.rollback()
    return count


async def deactivate_stale_hazards(session: AsyncSession, cutoff: datetime) -> int:
    """Mark hazards not refreshed since `cutoff` as inactive."""
    stmt = (
        update(HazardZone)
        .where(HazardZone.fetched_at < cutoff, HazardZone.is_active.is_(True))
        .values(is_active=False)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount  # type: ignore[return-value]


async def list_hazards_orm(session: AsyncSession, active_only: bool = True) -> list[HazardZone]:
    """Return ORM objects. Used internally and by API routes."""
    stmt = select(HazardZone)
    if active_only:
        stmt = stmt.where(HazardZone.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Integration contract (session-less — called by Person 2's scheduler)
# ---------------------------------------------------------------------------

async def get_active_hazards() -> list[dict]:
    """Return all active hazard zones as list of dicts.

    Self-managing session. No parameters.
    """
    async with async_session() as session:
        hazards = await list_hazards_orm(session, active_only=True)
        return [
            {
                "id": str(h.id),
                "event_type": h.event_type,
                "severity_weight": h.severity_weight,
                "title": h.title,
                "centroid_lat": h.centroid_lat,
                "centroid_lng": h.centroid_lng,
                "geometry_geojson": h.geometry_geojson,
            }
            for h in hazards
        ]
