"""
Risk Engine Scheduler.

Background async tasks that run the risk engine continuously:

1. Risk evaluation loop (every ~30 seconds):
   - Fetch all active trucks and hazard zones
   - Calculate risk scores for all pairs
   - Create risk_events for scores >= HIGH_RISK_THRESHOLD
   - Record risk history for trend tracking
   - Push alerts to Firebase (via Person 3's interface)

2. Forecast check loop (every ~10 minutes):
   - Fetch 24-hour weather forecasts for all truck positions
   - Flag dangerous conditions before trucks encounter them
   - Create forecast_alert records

Both loops are started as asyncio tasks on FastAPI startup and
cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from uuid import uuid4

from ..config import (
    CRITICAL_RISK_THRESHOLD,
    FORECAST_CHECK_INTERVAL_SEC,
    HIGH_RISK_THRESHOLD,
    RISK_ENGINE_INTERVAL_SEC,
)
from ..models.risk_models import (
    RiskEventCreate,
    RiskEventStatus,
    RiskScoreResult,
)
from .external.air_quality import fetch_aqi_batch
from .external.weather_forecast import (
    check_truck_forecasts,
    close_client as close_weather_client,
)
from .external.air_quality import (
    close_client as close_aqi_client,
)
from .risk_engine import evaluate_all_risks
from .risk_history import record_risk_snapshot

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Data Access Interface
# ═══════════════════════════════════════════════════════════════
# These are pluggable functions that Person 1 will provide.
# They default to no-ops so the scheduler can run standalone for testing.

# Type aliases for data access functions
GetTrucksFunc = Callable[[], Coroutine[Any, Any, List[Dict[str, Any]]]]
GetHazardsFunc = Callable[[], Coroutine[Any, Any, List[Dict[str, Any]]]]
SaveRiskEventFunc = Callable[[RiskEventCreate], Coroutine[Any, Any, str]]
PushAlertFunc = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]
SaveForecastAlertFunc = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


async def _default_get_trucks() -> List[Dict[str, Any]]:
    """Placeholder — returns empty list until Person 1 wires in DB access."""
    return []


async def _default_get_hazards() -> List[Dict[str, Any]]:
    """Placeholder — returns empty list until Person 1 wires in DB access."""
    return []


async def _default_save_risk_event(event: RiskEventCreate) -> str:
    """Placeholder — logs and returns a fake ID."""
    event_id = str(uuid4())
    logger.info("Would save risk event: truck=%s, score=%.4f → id=%s",
                event.truck_id, event.risk_score, event_id)
    return event_id


async def _default_push_alert(alert: Dict[str, Any]) -> None:
    """Placeholder — logs instead of pushing to Firebase."""
    logger.info("Would push alert to Firebase: %s", alert.get("truck_callsign", "unknown"))


async def _default_save_forecast_alert(alert: Dict[str, Any]) -> None:
    """Placeholder — logs instead of saving forecast alert."""
    logger.info("Would save forecast alert: %s", alert.get("forecast_type", "unknown"))


# ═══════════════════════════════════════════════════════════════
# Scheduler State
# ═══════════════════════════════════════════════════════════════

class RiskSchedulerState:
    """Holds the scheduler's running state and configuration."""

    def __init__(self):
        self.is_running: bool = False
        self.risk_task: Optional[asyncio.Task] = None
        self.forecast_task: Optional[asyncio.Task] = None

        # Pluggable data access functions
        self.get_trucks: GetTrucksFunc = _default_get_trucks
        self.get_hazards: GetHazardsFunc = _default_get_hazards
        self.save_risk_event: SaveRiskEventFunc = _default_save_risk_event
        self.push_alert: PushAlertFunc = _default_push_alert
        self.save_forecast_alert: SaveForecastAlertFunc = _default_save_forecast_alert

        # Stats
        self.last_evaluation_time: Optional[datetime] = None
        self.total_evaluations: int = 0
        self.total_events_created: int = 0

    def configure(
        self,
        get_trucks: Optional[GetTrucksFunc] = None,
        get_hazards: Optional[GetHazardsFunc] = None,
        save_risk_event: Optional[SaveRiskEventFunc] = None,
        push_alert: Optional[PushAlertFunc] = None,
        save_forecast_alert: Optional[SaveForecastAlertFunc] = None,
    ) -> None:
        """Wire in real data access functions from Person 1 / Person 3."""
        if get_trucks:
            self.get_trucks = get_trucks
        if get_hazards:
            self.get_hazards = get_hazards
        if save_risk_event:
            self.save_risk_event = save_risk_event
        if push_alert:
            self.push_alert = push_alert
        if save_forecast_alert:
            self.save_forecast_alert = save_forecast_alert


# Singleton state
_state = RiskSchedulerState()


def get_scheduler_state() -> RiskSchedulerState:
    """Get the scheduler singleton state."""
    return _state


# ═══════════════════════════════════════════════════════════════
# Risk Evaluation Loop
# ═══════════════════════════════════════════════════════════════


async def _risk_evaluation_cycle() -> int:
    """
    Run a single risk evaluation cycle.

    Returns the number of new risk events created.
    """
    state = _state

    # 1. Fetch current data
    trucks = await state.get_trucks()
    hazards = await state.get_hazards()

    if not trucks or not hazards:
        logger.debug("No trucks (%d) or hazards (%d) to evaluate",
                      len(trucks), len(hazards))
        return 0

    # 2. Build AQI cache for trucks near wildfire zones
    # Only fetch AQI for trucks that are somewhat close to hazards
    wildfire_hazards = [h for h in hazards if h.get("event_type") in ("wildfire", "hazardous_aqi")]
    if wildfire_hazards:
        truck_coords = [(t["lat"], t["lng"]) for t in trucks]
        aqi_cache = await fetch_aqi_batch(truck_coords)
    else:
        aqi_cache = {}

    # 3. Evaluate all truck × hazard combinations
    results = evaluate_all_risks(trucks, hazards, aqi_cache)

    # 4. Process results — create risk events for high/critical scores
    events_created = 0

    for item in results:
        score_result: RiskScoreResult = item["result"]

        # Record history for trend tracking
        record_risk_snapshot(
            truck_id=item["truck_id"],
            risk_score=score_result.risk_score,
            hazard_id=item["hazard_id"],
        )

        # Create risk event if score exceeds threshold
        if score_result.risk_score >= HIGH_RISK_THRESHOLD:
            event = RiskEventCreate(
                truck_id=item["truck_id"],
                hazard_id=item["hazard_id"],
                risk_score=score_result.risk_score,
                proximity_km=score_result.proximity_km,
                eta_to_hazard_min=score_result.eta_to_hazard_min,
                component_scores=score_result.component_scores.model_dump(),
                status=(
                    RiskEventStatus.PENDING
                    if score_result.risk_score < CRITICAL_RISK_THRESHOLD
                    else RiskEventStatus.PENDING  # Could auto-reroute CRITICALs
                ),
            )

            event_id = await state.save_risk_event(event)
            events_created += 1

            # Push real-time alert
            alert_data = {
                "event_id": event_id,
                "truck_id": item["truck_id"],
                "truck_callsign": item.get("truck_callsign", ""),
                "hazard_id": item["hazard_id"],
                "hazard_title": item.get("hazard_title", ""),
                "risk_score": score_result.risk_score,
                "risk_level": score_result.risk_level.value,
                "proximity_km": score_result.proximity_km,
                "eta_to_hazard_min": score_result.eta_to_hazard_min,
                "is_approaching": score_result.is_approaching,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            await state.push_alert(alert_data)

            logger.warning(
                "🚨 RISK EVENT: %s [%s] → score %.4f, %s, %.1fkm away, ETA %dmin",
                item.get("truck_callsign", "unknown"),
                score_result.risk_level.value,
                score_result.risk_score,
                item.get("hazard_title", "unknown"),
                score_result.proximity_km,
                score_result.eta_to_hazard_min,
            )

    state.last_evaluation_time = datetime.now(timezone.utc)
    state.total_evaluations += 1
    state.total_events_created += events_created

    logger.info(
        "Risk cycle #%d complete: %d events created from %d flagged results",
        state.total_evaluations,
        events_created,
        len(results),
    )

    return events_created


async def _risk_engine_loop() -> None:
    """
    Continuous risk evaluation loop.

    Runs every RISK_ENGINE_INTERVAL_SEC seconds until cancelled.
    """
    logger.info("🟢 Risk engine loop started (interval: %ds)", RISK_ENGINE_INTERVAL_SEC)

    while True:
        try:
            await _risk_evaluation_cycle()
        except asyncio.CancelledError:
            logger.info("Risk engine loop cancelled")
            raise
        except Exception as e:
            logger.error("Error in risk evaluation cycle: %s", e, exc_info=True)

        await asyncio.sleep(RISK_ENGINE_INTERVAL_SEC)


# ═══════════════════════════════════════════════════════════════
# Forecast Check Loop
# ═══════════════════════════════════════════════════════════════


async def _forecast_check_cycle() -> int:
    """
    Run a single forecast check cycle.

    Returns the number of forecast alerts created.
    """
    state = _state

    trucks = await state.get_trucks()
    if not trucks:
        return 0

    all_alerts = await check_truck_forecasts(trucks)
    alerts_created = 0

    for truck_id, alerts in all_alerts.items():
        for alert in alerts:
            alert_dict = alert.model_dump()
            alert_dict["id"] = str(uuid4())
            await state.save_forecast_alert(alert_dict)
            alerts_created += 1

    logger.info(
        "Forecast cycle complete: %d alerts created for %d trucks",
        alerts_created,
        len(all_alerts),
    )

    return alerts_created


async def _forecast_check_loop() -> None:
    """
    Continuous forecast check loop.

    Runs every FORECAST_CHECK_INTERVAL_SEC seconds until cancelled.
    """
    logger.info(
        "🟢 Forecast check loop started (interval: %ds)",
        FORECAST_CHECK_INTERVAL_SEC,
    )

    while True:
        try:
            await _forecast_check_cycle()
        except asyncio.CancelledError:
            logger.info("Forecast check loop cancelled")
            raise
        except Exception as e:
            logger.error("Error in forecast check cycle: %s", e, exc_info=True)

        await asyncio.sleep(FORECAST_CHECK_INTERVAL_SEC)


# ═══════════════════════════════════════════════════════════════
# Public API — Start / Stop
# ═══════════════════════════════════════════════════════════════


async def start_scheduler(
    get_trucks: Optional[GetTrucksFunc] = None,
    get_hazards: Optional[GetHazardsFunc] = None,
    save_risk_event: Optional[SaveRiskEventFunc] = None,
    push_alert: Optional[PushAlertFunc] = None,
    save_forecast_alert: Optional[SaveForecastAlertFunc] = None,
) -> None:
    """
    Start both the risk engine and forecast check loops.

    Call this from FastAPI's startup event:

        @app.on_event("startup")
        async def startup():
            await start_scheduler(
                get_trucks=db.get_active_trucks,
                get_hazards=db.get_active_hazards,
                save_risk_event=db.create_risk_event,
                push_alert=firebase.push_alert,
                save_forecast_alert=db.create_forecast_alert,
            )

    Args:
        All args are optional pluggable data access functions.
    """
    state = _state

    # Configure data access
    state.configure(
        get_trucks=get_trucks,
        get_hazards=get_hazards,
        save_risk_event=save_risk_event,
        push_alert=push_alert,
        save_forecast_alert=save_forecast_alert,
    )

    # Start background tasks
    state.risk_task = asyncio.create_task(_risk_engine_loop())
    state.forecast_task = asyncio.create_task(_forecast_check_loop())
    state.is_running = True

    logger.info("✅ Risk scheduler started successfully")


async def stop_scheduler() -> None:
    """
    Stop all scheduler loops and clean up resources.

    Call this from FastAPI's shutdown event:

        @app.on_event("shutdown")
        async def shutdown():
            await stop_scheduler()
    """
    state = _state

    if state.risk_task and not state.risk_task.done():
        state.risk_task.cancel()
        try:
            await state.risk_task
        except asyncio.CancelledError:
            pass

    if state.forecast_task and not state.forecast_task.done():
        state.forecast_task.cancel()
        try:
            await state.forecast_task
        except asyncio.CancelledError:
            pass

    # Close HTTP clients
    await close_weather_client()
    await close_aqi_client()

    state.is_running = False
    logger.info("🛑 Risk scheduler stopped")


async def run_single_evaluation() -> int:
    """
    Run a single risk evaluation cycle on-demand.

    Useful for testing and the demo scenario.
    Returns the number of risk events created.
    """
    return await _risk_evaluation_cycle()


async def run_single_forecast_check() -> int:
    """
    Run a single forecast check cycle on-demand.

    Returns the number of forecast alerts created.
    """
    return await _forecast_check_cycle()
