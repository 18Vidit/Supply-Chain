"""Supply Chain Intelligence Platform backend.

Run locally:
    cd backend
    set PYTHONPATH=.
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from app.domain.global_network import get_global_network_payload
from app.firebase.realtime_db import initialize_firebase, push_json
from app.routing.route_optimizer import get_detour_route
from app.services.ai_engine import answer_dispatcher_query, build_decision_brief, generate_alert
from app.services.cascade_predictor import calculate_cascade
from app.services.condition_providers import assess_route_conditions, get_weather_condition
from app.services.hazard_poller import get_all_hazards
from app.services.optimization_engine import build_risk_analysis, optimize_route_for_truck, predict_delay
from app.services.risk_engine import evaluate_risk
from app.simulator.truck_simulator import (
    get_trucks,
    reroute_truck,
    set_simulation_speed,
    start_simulation,
    stop_simulation,
    trucks as all_trucks_list,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Supply Chain Intelligence Platform",
    description="Global logistics risk, route optimization, and AI decision support.",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    start_simulation()
    initialize_firebase()
    logger.info("Global fleet simulator started with %d active shipments.", len(all_trucks_list))


@app.on_event("shutdown")
async def shutdown() -> None:
    stop_simulation()


@app.get("/health")
async def health() -> Dict[str, Any]:
    network = get_global_network_payload()
    return {
        "status": "ok",
        "version": app.version,
        "shipments": len(all_trucks_list),
        "countries": network["stats"]["country_count"],
        "ports": network["stats"]["port_count"],
        "lanes": network["stats"]["lane_count"],
        "ai_mode": os.getenv("AI_PROVIDER", "offline"),
    }


@app.get("/global-network")
async def api_global_network() -> Dict[str, Any]:
    """Countries, ports, lanes, and global import/export operating model."""
    return get_global_network_payload()


@app.get("/analytics")
async def api_analytics() -> Dict[str, Any]:
    """Dashboard-ready KPI and risk analytics."""
    trucks = get_trucks()
    hazards = get_all_hazards()
    risks = evaluate_risk()
    network = get_global_network_payload()
    risk_analysis = build_risk_analysis(trucks, risks, hazards)
    critical = [risk for risk in risks if risk.get("risk_label") == "CRITICAL"]
    high = [risk for risk in risks if risk.get("risk_label") == "HIGH"]
    delayed = _fast_delay_predictions(trucks, risks)
    return {
        "kpis": {
            "active_shipments": len(trucks),
            "countries": network["stats"]["country_count"],
            "ports": network["stats"]["port_count"],
            "active_hazards": len(hazards),
            "critical": len(critical),
            "high": len(high),
            "value_at_risk_usd": risk_analysis["value_at_risk_usd"],
            "network_value_usd": risk_analysis["total_network_value_usd"],
        },
        "network": network["stats"],
        "risk": risk_analysis,
        "delay_predictions": delayed[:8],
        "mode_mix": _count_by(trucks, "transport_category"),
        "flow_mix": _count_by(trucks, "flow_type"),
        "country_mix": _country_mix(trucks),
    }


@app.get("/trucks")
async def api_trucks() -> List[Dict[str, Any]]:
    """Return all active global shipments."""
    return get_trucks()


@app.get("/hazards")
async def api_hazards() -> List[Dict[str, Any]]:
    """Return active hazards from live sources or deterministic offline baseline."""
    return get_all_hazards()


@app.get("/risk")
async def api_risk() -> List[Dict[str, Any]]:
    """Evaluate risk for all shipments against active hazards."""
    return evaluate_risk()


@app.get("/conditions/live")
async def api_conditions(truck_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Live weather/traffic condition snapshot for a shipment or sample network nodes."""
    trucks = get_trucks()
    if truck_id:
        truck = _truck_or_none(trucks, truck_id)
        if not truck:
            return {"error": f"Truck {truck_id} not found"}
        conditions = assess_route_conditions(_points_from_truck(truck), truck.get("mode", "road"))
        return {"truck_id": truck_id, "callsign": truck.get("callsign"), "conditions": conditions}

    sample = []
    for truck in trucks[:8]:
        sample.append(
            {
                "truck_id": truck["id"],
                "callsign": truck["callsign"],
                "lane_id": truck.get("lane_id"),
                "weather": get_weather_condition(float(truck["lat"]), float(truck["lng"])),
            }
        )
    return {"samples": sample}


@app.get("/ai-alerts")
async def api_ai_alerts() -> List[Dict[str, Any]]:
    """Decision-support alert summaries for high and critical shipments."""
    risks = evaluate_risk()
    high_risk = [r for r in risks if r["risk_label"] in ("HIGH", "CRITICAL")]
    if not high_risk:
        return []

    truck_map = {t["id"]: t for t in get_trucks()}
    alerts = []
    for risk in high_risk[:8]:
        truck = truck_map.get(risk["truck_id"], {})
        if not truck:
            continue
        cascade = calculate_cascade(
            rerouted_truck_id=risk["truck_id"],
            delay_minutes=risk.get("eta_min", 60),
            all_trucks=get_trucks(),
        )
        message = generate_alert(truck, risk, cascade)
        alert = {
            "truck_id": risk["truck_id"],
            "callsign": truck.get("callsign", risk["truck_id"]),
            "risk_score": risk["risk_score"],
            "risk_label": risk["risk_label"],
            "message": message,
            "route_name": truck.get("route_name", ""),
            "lane_id": truck.get("lane_id"),
            "eta_min": risk.get("eta_min", 0),
            "proximity_km": risk.get("proximity_km", 0),
            "hazard_title": risk.get("hazard_title", ""),
            "cascade": cascade,
        }
        alerts.append(alert)
        push_json("/alerts", alert)
    return alerts


@app.get("/route/optimize/{truck_id}")
async def api_optimize_route(truck_id: str) -> Dict[str, Any]:
    truck = _truck_or_none(get_trucks(), truck_id)
    if not truck:
        return {"error": f"Truck {truck_id} not found"}
    risk = _risk_for_truck(evaluate_risk(), truck_id)
    hazard = _hazard_for_risk(risk)
    return optimize_route_for_truck(truck, hazard)


@app.get("/ai/brief/{truck_id}")
async def api_ai_brief(truck_id: str) -> Dict[str, Any]:
    truck = _truck_or_none(get_trucks(), truck_id)
    if not truck:
        return {"error": f"Truck {truck_id} not found"}
    risk = _risk_for_truck(evaluate_risk(), truck_id)
    hazard = _hazard_for_risk(risk)
    return build_decision_brief(truck, risk, hazard)


@app.get("/detour/{truck_id}")
async def api_detour(truck_id: str) -> Dict[str, Any]:
    """Compute and apply the best condition-aware detour for a shipment."""
    truck = _truck_or_none(get_trucks(), truck_id)
    if not truck:
        return {"error": f"Truck {truck_id} not found"}

    risk = _risk_for_truck(evaluate_risk(), truck_id)
    hazard = _hazard_for_risk(risk)
    detour = get_detour_route(truck, hazard or {})
    best_plan = {
        "label": detour.get("decision") or detour.get("strategy", "optimized"),
        "coordinates": detour.get("coordinates", []),
        "cost_usd": detour.get("cost_usd"),
        "risk_index": detour.get("risk_index"),
    }
    if detour.get("coordinates"):
        last = detour["coordinates"][-1]
        reroute_truck(truck_id, float(last[0]), float(last[1]), route_plan=best_plan)
        push_json("/route_decisions", {"truck_id": truck_id, "detour": detour})

    cascade = calculate_cascade(
        rerouted_truck_id=truck_id,
        delay_minutes=detour.get("extra_min", 30),
        all_trucks=get_trucks(),
    )
    return {"truck_id": truck_id, "callsign": truck.get("callsign", truck_id), "detour": detour, "cascade": cascade}


@app.get("/reroute-all-critical")
async def api_reroute_all_critical() -> Dict[str, Any]:
    """Automatically reroute all CRITICAL-risk shipments."""
    risks = evaluate_risk()
    critical = [r for r in risks if r["risk_label"] == "CRITICAL"]
    truck_map = {t["id"]: t for t in get_trucks()}
    rerouted = []

    for risk in critical:
        truck = truck_map.get(risk["truck_id"])
        if not truck:
            continue
        hazard = _hazard_for_risk(risk)
        optimization = optimize_route_for_truck(truck, hazard)
        best = optimization.get("best_route") or {}
        coordinates = best.get("coordinates") or []
        if not coordinates:
            continue
        last = coordinates[-1]
        if reroute_truck(risk["truck_id"], float(last[0]), float(last[1]), route_plan=best):
            rerouted.append({"truck_id": risk["truck_id"], "callsign": truck.get("callsign"), "risk_score": risk["risk_score"], "route": best.get("label")})

    return {"rerouted": len(rerouted), "trucks": rerouted}


@app.get("/ask")
async def api_ask(q: str = Query(..., description="Dispatcher question")) -> Dict[str, Any]:
    risks = evaluate_risk()
    truck_list = get_trucks()
    high_risk = [r for r in risks if r["risk_label"] in ("HIGH", "CRITICAL")]
    return {"answer": answer_dispatcher_query(q, high_risk, truck_list)}


@app.post("/simulation/speed/{multiplier}")
async def api_set_simulation_speed(multiplier: float) -> Dict[str, Any]:
    applied = set_simulation_speed(multiplier)
    return {"simulation_speed": applied}


def _truck_or_none(trucks: List[Dict[str, Any]], truck_id: str) -> Optional[Dict[str, Any]]:
    return next((truck for truck in trucks if str(truck.get("id")) == str(truck_id) or truck.get("callsign") == truck_id), None)


def _risk_for_truck(risks: List[Dict[str, Any]], truck_id: str) -> Optional[Dict[str, Any]]:
    return next((risk for risk in risks if str(risk.get("truck_id")) == str(truck_id)), None)


def _hazard_for_risk(risk: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not risk:
        return None
    hazard_id = risk.get("hazard_id")
    return next((hazard for hazard in get_all_hazards() if str(hazard.get("id")) == str(hazard_id)), None)


def _points_from_truck(truck: Dict[str, Any]) -> List[tuple[float, float]]:
    return [(float(point[0]), float(point[1])) for point in truck.get("route_polyline", [])]


def _count_by(items: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _country_mix(trucks: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for truck in trucks:
        for key in ("origin_country", "destination_country"):
            value = str(truck.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
    return counts


def _fast_delay_predictions(trucks: List[Dict[str, Any]], risks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Low-latency dashboard delay estimates; detailed briefs use full optimization."""
    risk_by_truck = {str(risk.get("truck_id")): risk for risk in risks}
    predictions = []
    for truck in trucks:
        risk = risk_by_truck.get(str(truck.get("id")), {})
        score = float(risk.get("risk_score", 0.0))
        service_multiplier = {
            "expedited": 1.1,
            "just_in_time": 1.25,
            "temperature_controlled": 1.15,
            "cold_chain": 1.12,
        }.get(str(truck.get("service_level", "")), 1.0)
        delay = score * 95.0 * service_multiplier
        label = "ON_TIME"
        if delay >= 120:
            label = "SEVERE_DELAY"
        elif delay >= 60:
            label = "DELAY_LIKELY"
        elif delay >= 20:
            label = "WATCH"
        predictions.append(
            {
                "truck_id": truck.get("id"),
                "callsign": truck.get("callsign"),
                "predicted_delay_min": round(delay, 1),
                "delay_label": label,
                "confidence": 0.68,
                "drivers": {"risk_score": round(score, 3), "service_level": truck.get("service_level")},
                "recommended_route_id": "open-brief",
            }
        )
    predictions.sort(key=lambda item: item["predicted_delay_min"], reverse=True)
    return predictions[:8]


_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    logger.info("Frontend mounted from %s", os.path.abspath(_frontend_dir))
