"""AI decision-support layer for logistics operations.

Gemini is optional. If GEMINI_API_KEY is absent or AI_PROVIDER=offline, the
module uses a local knowledge-based decision engine so sensitive fleet data can
still be analyzed without leaving the machine.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

import requests

from .optimization_engine import optimize_route_for_truck, predict_delay

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def generate_alert(
    truck: Mapping[str, Any],
    risk: Mapping[str, Any],
    cascade: Mapping[str, Any] | None = None,
) -> str:
    """Generate a concise decision-grade alert for a high-risk shipment."""
    callsign = truck.get("callsign", risk.get("truck_id", "Unknown truck"))
    hazard = risk.get("hazard_title") or "nearby disruption"
    label = risk.get("risk_label", risk.get("risk_level", "UNKNOWN"))
    score = float(risk.get("risk_score", 0.0))
    proximity = float(risk.get("proximity_km", 0.0))
    eta = int(risk.get("eta_min", risk.get("eta_to_hazard_min", 0)))
    affected = int((cascade or {}).get("affected_count", 0) or (cascade or {}).get("affected_delivery_count", 0))
    predicted_delay = max(0, round(score * 90 + (30 if label == "CRITICAL" else 0) - min(eta, 60) * 0.2))
    delay_text = f"{predicted_delay:.0f} min predicted delay"
    cascade_text = f"; {affected} downstream deliveries may shift" if affected else ""
    return (
        f"{callsign} is {label} near {hazard}. "
        f"Distance {proximity:.1f} km, hazard ETA {eta} min, {delay_text}{cascade_text}. "
        "Recommended action: open route brief before committing reroute."
    )


def answer_dispatcher_query(
    query: str,
    high_risk: Iterable[Mapping[str, Any]],
    truck_list: Iterable[Mapping[str, Any]],
) -> str:
    """Answer operational questions using route, risk, cost, and delay context."""
    q = query.lower().strip()
    risks = list(high_risk)
    trucks = list(truck_list)

    if not q:
        return "Ask about route choices, delay predictions, cost exposure, or priority shipments."

    gemini_answer = _try_gemini(query, risks, trucks)
    if gemini_answer:
        return gemini_answer

    if "cost" in q or "value" in q or "savings" in q:
        return _cost_answer(risks, trucks)

    if "delay" in q or "late" in q or "eta" in q:
        return _delay_answer(risks, trucks)

    if "reroute" in q or "route" in q or "priority" in q or "should" in q:
        return _route_answer(risks, trucks)

    if "country" in q or "port" in q or "global" in q or "lane" in q:
        lanes = sorted({str(t.get("lane_id", "unknown")) for t in trucks})
        countries = sorted({str(t.get("origin_country", "")) for t in trucks} | {str(t.get("destination_country", "")) for t in trucks})
        return f"Network coverage spans {len(countries)} country codes across {len(lanes)} active lanes: {', '.join(lanes[:6])}."

    if "risk" in q or "hazard" in q or "why" in q:
        return _risk_answer(risks)

    on_route = sum(1 for t in trucks if t.get("status") == "ON_ROUTE")
    rerouted = sum(1 for t in trucks if t.get("status") == "REROUTED")
    return f"Fleet status: {len(trucks)} shipments, {on_route} on committed lanes, {rerouted} rerouted, {len(risks)} elevated-risk shipments."


def build_decision_brief(
    truck: Mapping[str, Any],
    risk: Optional[Mapping[str, Any]] = None,
    disruption: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return structured decision support for one truck."""
    optimization = optimize_route_for_truck(truck, disruption)
    delay = predict_delay(truck, risk)
    best = optimization.get("best_route") or {}
    return {
        "truck_id": str(truck.get("id", "")),
        "callsign": truck.get("callsign", truck.get("id", "")),
        "route_recommendation": optimization.get("decision"),
        "recommended_route": best,
        "delay_prediction": delay,
        "explanation": _explain_route_decision(truck, risk, best),
        "mode": _mode(),
    }


def suggest_route(truck: Mapping[str, Any]) -> str:
    """Compatibility helper retained for older UI code."""
    brief = build_decision_brief(truck)
    return str(brief.get("route_recommendation", "Stay on current lane."))


def chatbot(query: str) -> str:
    """Compatibility helper retained for older demos."""
    return answer_dispatcher_query(query, [], [])


def _try_gemini(query: str, risks: List[Mapping[str, Any]], trucks: List[Mapping[str, Any]]) -> Optional[str]:
    if _mode() != "gemini":
        return None
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    context = {
        "query": query,
        "fleet_size": len(trucks),
        "high_risk": list(risks)[:8],
        "sample_shipments": [
            {
                "id": t.get("id"),
                "callsign": t.get("callsign"),
                "lane_id": t.get("lane_id"),
                "origin_country": t.get("origin_country"),
                "destination_country": t.get("destination_country"),
                "mode": t.get("mode"),
                "service_level": t.get("service_level"),
                "projected_cost_usd": t.get("projected_cost_usd"),
            }
            for t in trucks[:12]
        ],
    }
    prompt = (
        "You are a logistics decision-support system. Answer with operational "
        "recommendations, not small talk. Use cost, delay, route, and risk context. "
        f"Context JSON: {json.dumps(context, default=str)}"
    )
    try:
        response = requests.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 220}},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = " ".join(part.get("text", "") for part in parts).strip()
        return text or None
    except Exception:
        return None


def _mode() -> str:
    provider = os.getenv("AI_PROVIDER", "offline").strip().lower()
    return "gemini" if provider == "gemini" and os.getenv("GEMINI_API_KEY") else "offline"


def _cost_answer(risks: List[Mapping[str, Any]], trucks: List[Mapping[str, Any]]) -> str:
    risk_ids = {str(r.get("truck_id")) for r in risks}
    total = sum(float(t.get("projected_cost_usd", t.get("base_cost_usd", 0))) for t in trucks)
    exposed = sum(float(t.get("projected_cost_usd", t.get("base_cost_usd", 0))) for t in trucks if str(t.get("id")) in risk_ids)
    ratio = (exposed / total * 100.0) if total else 0.0
    return f"Estimated shipment value at risk is ${exposed:,.0f}, about {ratio:.1f}% of the visible network value."


def _delay_answer(risks: List[Mapping[str, Any]], trucks: List[Mapping[str, Any]]) -> str:
    if not trucks:
        return "No shipment data is loaded."
    risk_by_truck = {str(r.get("truck_id")): r for r in risks}
    predictions = [predict_delay(truck, risk_by_truck.get(str(truck.get("id")))) for truck in trucks[:20]]
    predictions.sort(key=lambda item: item["predicted_delay_min"], reverse=True)
    top = predictions[:3]
    if not top:
        return "No delay predictions are available."
    text = "; ".join(f"{item['callsign']} {item['predicted_delay_min']:.0f} min ({item['delay_label']})" for item in top)
    return f"Highest predicted delays: {text}."


def _route_answer(risks: List[Mapping[str, Any]], trucks: List[Mapping[str, Any]]) -> str:
    if not risks:
        return "No elevated-risk shipments currently need rerouting; keep monitoring live conditions."
    risk_by_truck = {str(r.get("truck_id")): r for r in risks}
    ordered = sorted(risks, key=lambda r: float(r.get("risk_score", 0)), reverse=True)
    recs = []
    for risk in ordered[:3]:
        truck = next((t for t in trucks if str(t.get("id")) == str(risk.get("truck_id"))), None)
        if not truck:
            continue
        brief = build_decision_brief(truck, risk)
        recs.append(f"{truck.get('callsign')}: {brief['route_recommendation']}")
    return " ".join(recs) if recs else "No reroute candidates could be matched to loaded shipments."


def _risk_answer(risks: List[Mapping[str, Any]]) -> str:
    if not risks:
        return "No elevated hazard exposure is detected for the current fleet snapshot."
    top = max(risks, key=lambda r: float(r.get("risk_score", 0)))
    return (
        f"{top.get('callsign') or top.get('truck_id')} is the top concern: "
        f"{top.get('risk_label')} score {float(top.get('risk_score', 0)):.2f}, "
        f"{top.get('proximity_km', 0)} km from {top.get('hazard_title', 'a disruption')}."
    )


def _explain_route_decision(
    truck: Mapping[str, Any],
    risk: Optional[Mapping[str, Any]],
    best: Mapping[str, Any],
) -> str:
    risk_score = float((risk or {}).get("risk_score", 0.0))
    delay = float(best.get("delay_min", 0.0))
    cost_delta = float(best.get("cost_delta_usd", 0.0))
    lane = truck.get("lane_id", "lane")
    return (
        f"{lane} was scored on duration, live condition risk, cost, and reliability. "
        f"Best option risk index {float(best.get('risk_index', 0.0)):.2f}, "
        f"expected delay {delay:.0f} min, cost delta ${cost_delta:,.0f}, "
        f"shipment risk score {risk_score:.2f}."
    )
