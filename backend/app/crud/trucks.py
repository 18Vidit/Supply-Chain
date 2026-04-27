from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db_models import Truck
from ..database import async_session


async def create_truck(session: AsyncSession, payload: dict) -> Truck:
    """Create a truck from a dict payload. Used by API route (session injected)."""
    truck = Truck(**payload)
    session.add(truck)
    await session.commit()
    await session.refresh(truck)
    return truck


async def list_trucks_orm(session: AsyncSession, active_only: bool = True) -> list[Truck]:
    """Return ORM objects. Used internally and by API routes."""
    stmt = select(Truck)
    if active_only:
        stmt = stmt.where(Truck.status != "STOPPED")
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_active_trucks() -> list[dict]:
    """Integration contract for Person 2's scheduler. Self-managing session, no params.

    Returns all trucks with status != 'STOPPED' as list of dicts.
    """
    async with async_session() as session:
        trucks = await list_trucks_orm(session, active_only=True)
        return [
            {
                "id": str(t.id),
                "callsign": t.callsign,
                "lat": t.lat,
                "lng": t.lng,
                "speed_kmh": t.speed_kmh,
                "heading_deg": t.heading_deg,
                "cargo_type": t.cargo_type,
                "cargo_priority": t.cargo_priority,
                "origin": t.origin,
                "destination": t.destination,
                "eta": t.eta.isoformat() if t.eta else None,
            }
            for t in trucks
        ]
