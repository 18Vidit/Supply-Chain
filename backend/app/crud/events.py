import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db_models import RiskEvent
from ..database import async_session

logger = logging.getLogger(__name__)


async def save_risk_event(event) -> str:
    """Integration contract for Person 2's scheduler.

    Accepts Person 2's RiskEventCreate Pydantic model (or any object with
    model_dump()). Manages its own session. Returns UUID string of the
    created record.
    """
    # Support both Pydantic models (.model_dump()) and plain dicts
    if hasattr(event, "model_dump"):
        data = event.model_dump()
    elif isinstance(event, dict):
        data = event
    else:
        data = dict(event)

    # Only pick fields that exist on our RiskEvent ORM model
    allowed = {
        "truck_id", "hazard_id", "risk_score", "proximity_km",
        "eta_to_hazard_min", "component_scores", "status",
    }
    filtered = {k: v for k, v in data.items() if k in allowed}

    async with async_session() as session:
        row = RiskEvent(**filtered)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return str(row.id)


async def list_risk_events_orm(session: AsyncSession, limit: int = 100) -> list[RiskEvent]:
    """Return ORM objects. Used by API route."""
    stmt = select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
