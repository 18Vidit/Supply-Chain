"""CRUD API endpoints — Person 1's data routes.

Router prefix: /api (trucks, hazards, events)
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..crud.db_models import Truck, HazardZone, RiskEvent
from ..crud.trucks import create_truck, list_trucks_orm
from ..crud.hazards import list_hazards_orm
from ..crud.events import list_risk_events_orm
from ..database import get_session

router = APIRouter(prefix="/api", tags=["data"])


# ---- Trucks ----

@router.get("/trucks")
async def get_trucks(session: AsyncSession = Depends(get_session)):
    trucks = await list_trucks_orm(session)
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
            "status": t.status,
            "origin": t.origin,
            "destination": t.destination,
            "eta": t.eta.isoformat() if t.eta else None,
            "route_polyline": t.route_polyline,
            "last_updated": t.last_updated.isoformat() if t.last_updated else None,
        }
        for t in trucks
    ]


@router.post("/trucks", status_code=201)
async def post_truck(payload: dict, session: AsyncSession = Depends(get_session)):
    truck = await create_truck(session, payload)
    return {
        "id": str(truck.id),
        "callsign": truck.callsign,
        "lat": truck.lat,
        "lng": truck.lng,
        "status": truck.status,
    }


# ---- Hazards ----

@router.get("/hazards")
async def get_hazards(session: AsyncSession = Depends(get_session)):
    hazards = await list_hazards_orm(session)
    return [
        {
            "id": str(h.id),
            "source_api": h.source_api,
            "external_id": h.external_id,
            "event_type": h.event_type,
            "title": h.title,
            "severity_weight": h.severity_weight,
            "geometry_geojson": h.geometry_geojson,
            "centroid_lat": h.centroid_lat,
            "centroid_lng": h.centroid_lng,
            "is_active": h.is_active,
            "fetched_at": h.fetched_at.isoformat() if h.fetched_at else None,
        }
        for h in hazards
    ]


# ---- Risk Events ----

@router.get("/events")
async def get_events(session: AsyncSession = Depends(get_session)):
    events = await list_risk_events_orm(session)
    return [
        {
            "id": str(e.id),
            "truck_id": str(e.truck_id),
            "hazard_id": str(e.hazard_id),
            "risk_score": e.risk_score,
            "proximity_km": e.proximity_km,
            "eta_to_hazard_min": e.eta_to_hazard_min,
            "component_scores": e.component_scores,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
