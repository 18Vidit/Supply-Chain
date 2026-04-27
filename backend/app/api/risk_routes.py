"""
Risk Engine API Routes.

FastAPI router providing endpoints for:
- On-demand risk evaluation
- Risk event management (list, approve, dismiss)
- Cascade impact queries
- Forecast alerts
- Dashboard statistics
- Scheduler control

These endpoints are consumed by Person 4's frontend dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..config import HIGH_RISK_THRESHOLD, CRITICAL_RISK_THRESHOLD
from ..models.risk_models import (
    CascadeImpactResult,
    ForecastAlertData,
    RiskDashboardStats,
    RiskEventResponse,
    RiskEventStatus,
    RiskLevel,
    RiskScoreResult,
    RiskTrendData,
)
from ..services.cascade_engine import calculate_cascade_impact
from ..services.risk_engine import calculate_risk_score
from ..services.risk_history import (
    get_all_trends,
    get_risk_summary_for_gemini,
    get_risk_trend,
)
from ..services.risk_scheduler import (
    get_scheduler_state,
    run_single_evaluation,
    run_single_forecast_check,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["Risk Engine"])

# ═══════════════════════════════════════════════════════════════
# In-memory stores (will be replaced with DB access from Person 1)
# ═══════════════════════════════════════════════════════════════

# Temporary in-memory storage for risk events and forecast alerts.
# Person 1 will provide proper DB CRUD — these are drop-in replacements.
_risk_events: Dict[str, Dict[str, Any]] = {}
_forecast_alerts: Dict[str, Dict[str, Any]] = {}


# ═══════════════════════════════════════════════════════════════
# Risk Evaluation Endpoints
# ═══════════════════════════════════════════════════════════════


@router.post("/evaluate", response_model=Dict[str, Any])
async def trigger_evaluation():
    """
    Trigger an on-demand risk evaluation cycle.

    Runs the risk engine once across all truck-hazard pairs.
    Returns the number of new risk events created.
    """
    events_created = await run_single_evaluation()
    return {
        "status": "completed",
        "events_created": events_created,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/evaluate/{truck_id}", response_model=RiskScoreResult)
async def evaluate_single_truck(truck_id: str):
    """
    Evaluate risk for a single truck against all active hazards.

    This is the on-demand endpoint for the frontend to check
    a specific truck's risk without waiting for the scheduler.
    """
    state = get_scheduler_state()

    # Get truck data
    trucks = await state.get_trucks()
    truck = next((t for t in trucks if t.get("id") == truck_id), None)
    if not truck:
        raise HTTPException(status_code=404, detail=f"Truck {truck_id} not found")

    # Get hazards
    hazards = await state.get_hazards()
    if not hazards:
        raise HTTPException(status_code=404, detail="No active hazards")

    # Calculate risk against each hazard, return the highest
    highest_score = None
    for hazard in hazards:
        result = calculate_risk_score(truck, hazard)
        if highest_score is None or result.risk_score > highest_score.risk_score:
            highest_score = result

    if highest_score is None:
        raise HTTPException(status_code=404, detail="No risk scores calculated")

    return highest_score


# ═══════════════════════════════════════════════════════════════
# Risk Event CRUD Endpoints
# ═══════════════════════════════════════════════════════════════


@router.get("/events", response_model=List[Dict[str, Any]])
async def list_risk_events(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, APPROVED, DISMISSED, AUTO_REROUTED"),
    truck_id: Optional[str] = Query(None, description="Filter by truck ID"),
    min_score: Optional[float] = Query(None, description="Minimum risk score filter"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
):
    """
    List risk events with optional filtering.

    Returns events sorted by risk score (highest first).
    """
    events = list(_risk_events.values())

    # Apply filters
    if status:
        events = [e for e in events if e.get("status") == status]
    if truck_id:
        events = [e for e in events if e.get("truck_id") == truck_id]
    if min_score is not None:
        events = [e for e in events if e.get("risk_score", 0) >= min_score]

    # Sort by risk score descending
    events.sort(key=lambda e: e.get("risk_score", 0), reverse=True)

    return events[:limit]


@router.get("/events/{event_id}")
async def get_risk_event(event_id: str):
    """Get a specific risk event by ID."""
    event = _risk_events.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Risk event {event_id} not found")
    return event


@router.post("/events/{event_id}/approve")
async def approve_reroute(event_id: str):
    """
    Dispatcher approves the suggested reroute for a risk event.

    This triggers Person 3's routing system to execute the reroute.
    """
    event = _risk_events.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Risk event {event_id} not found")

    if event.get("status") != RiskEventStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Event is {event['status']}, not PENDING"
        )

    event["status"] = RiskEventStatus.APPROVED.value
    event["resolved_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("✅ Reroute APPROVED for event %s (truck %s)",
                event_id, event.get("truck_id"))

    # TODO: Trigger Person 3's reroute execution
    # await routing_service.execute_reroute(event)

    return {
        "status": "approved",
        "event_id": event_id,
        "message": "Reroute approved and queued for execution",
    }


@router.post("/events/{event_id}/dismiss")
async def dismiss_event(event_id: str):
    """Dispatcher dismisses a risk event (no reroute needed)."""
    event = _risk_events.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Risk event {event_id} not found")

    event["status"] = RiskEventStatus.DISMISSED.value
    event["resolved_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("❌ Event DISMISSED: %s (truck %s)",
                event_id, event.get("truck_id"))

    return {
        "status": "dismissed",
        "event_id": event_id,
    }


# ═══════════════════════════════════════════════════════════════
# Cascade Impact Endpoints
# ═══════════════════════════════════════════════════════════════


@router.get("/cascade/{event_id}", response_model=CascadeImpactResult)
async def get_cascade_impact(event_id: str):
    """
    Get the cascade impact analysis for a risk event.

    Shows which downstream deliveries would be affected if
    this truck is rerouted.
    """
    event = _risk_events.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Risk event {event_id} not found")

    state = get_scheduler_state()
    all_trucks = await state.get_trucks()

    # Build rerouted truck dict for cascade calculation
    rerouted_truck = {
        "id": event.get("truck_id"),
        "callsign": event.get("truck_callsign", ""),
        "destination": event.get("truck_destination", ""),
        "time_delta_min": event.get("time_delta_min", 60),  # default 60 min detour
    }

    cascade = calculate_cascade_impact(rerouted_truck, all_trucks)
    return cascade


# ═══════════════════════════════════════════════════════════════
# Forecast Alert Endpoints
# ═══════════════════════════════════════════════════════════════


@router.get("/forecast/alerts", response_model=List[Dict[str, Any]])
async def list_forecast_alerts(
    truck_id: Optional[str] = Query(None, description="Filter by truck ID"),
    active_only: bool = Query(True, description="Only return active alerts"),
):
    """List proactive weather forecast alerts."""
    alerts = list(_forecast_alerts.values())

    if truck_id:
        alerts = [a for a in alerts if a.get("truck_id") == truck_id]
    if active_only:
        alerts = [a for a in alerts if a.get("is_active", True)]

    # Sort by hours_ahead ascending (soonest first)
    alerts.sort(key=lambda a: a.get("hours_ahead", 999))

    return alerts


@router.post("/forecast/check")
async def trigger_forecast_check():
    """Trigger an on-demand forecast check for all trucks."""
    alerts_created = await run_single_forecast_check()
    return {
        "status": "completed",
        "alerts_created": alerts_created,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# Risk Trends Endpoints
# ═══════════════════════════════════════════════════════════════


@router.get("/trends/{truck_id}", response_model=RiskTrendData)
async def get_truck_risk_trend(truck_id: str):
    """Get the risk trend for a specific truck."""
    return get_risk_trend(truck_id)


@router.get("/trends/{truck_id}/summary")
async def get_truck_trend_summary(truck_id: str, callsign: str = ""):
    """
    Get a natural-language risk trend summary for a truck.

    Used by the Gemini integration for context injection.
    """
    summary = get_risk_summary_for_gemini(truck_id, callsign)
    return {"truck_id": truck_id, "summary": summary}


@router.get("/trends", response_model=Dict[str, RiskTrendData])
async def get_all_risk_trends():
    """Get risk trends for all tracked trucks."""
    return get_all_trends()


# ═══════════════════════════════════════════════════════════════
# Dashboard Statistics
# ═══════════════════════════════════════════════════════════════


@router.get("/dashboard", response_model=RiskDashboardStats)
async def get_dashboard_stats():
    """
    Get summary statistics for the risk dashboard.

    Returns counts of events by status, risk level, and aggregate metrics.
    """
    events = list(_risk_events.values())

    if not events:
        return RiskDashboardStats()

    scores = [e.get("risk_score", 0) for e in events]
    active_events = [e for e in events if e.get("status") == RiskEventStatus.PENDING.value]

    return RiskDashboardStats(
        total_active_events=len(active_events),
        critical_count=sum(1 for s in scores if s >= CRITICAL_RISK_THRESHOLD),
        high_count=sum(1 for s in scores if HIGH_RISK_THRESHOLD <= s < CRITICAL_RISK_THRESHOLD),
        moderate_count=sum(1 for s in scores if 0.40 <= s < HIGH_RISK_THRESHOLD),
        pending_count=sum(1 for e in events if e.get("status") == RiskEventStatus.PENDING.value),
        approved_count=sum(1 for e in events if e.get("status") == RiskEventStatus.APPROVED.value),
        auto_rerouted_count=sum(1 for e in events if e.get("status") == RiskEventStatus.AUTO_REROUTED.value),
        dismissed_count=sum(1 for e in events if e.get("status") == RiskEventStatus.DISMISSED.value),
        avg_risk_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
        max_risk_score=round(max(scores), 4) if scores else 0.0,
        trucks_at_risk=len(set(e.get("truck_id") for e in active_events)),
        active_forecast_alerts=sum(1 for a in _forecast_alerts.values() if a.get("is_active")),
        total_cascade_delay_hours=sum(
            e.get("cascade_impact", {}).get("total_cascade_delay_hours", 0)
            for e in events
        ),
    )


# ═══════════════════════════════════════════════════════════════
# Scheduler Control
# ═══════════════════════════════════════════════════════════════


@router.get("/scheduler/status")
async def get_scheduler_status():
    """Get the current status of the risk engine scheduler."""
    state = get_scheduler_state()
    return {
        "is_running": state.is_running,
        "total_evaluations": state.total_evaluations,
        "total_events_created": state.total_events_created,
        "last_evaluation_time": (
            state.last_evaluation_time.isoformat()
            if state.last_evaluation_time
            else None
        ),
    }
