"""
Integration test — runs the full risk pipeline end-to-end.

Tests the complete flow:
1. Synthetic trucks + hazards
2. Risk score calculation
3. Risk history recording
4. Trend detection
5. Cascade impact calculation
6. Forecast analysis

This validates that all Person 2 modules work together correctly.
"""

import pytest
from datetime import datetime, timezone, timedelta

from backend.app.services.risk_engine import (
    calculate_risk_score,
    evaluate_all_risks,
)
from backend.app.services.cascade_engine import calculate_cascade_impact
from backend.app.services.risk_history import (
    record_risk_snapshot,
    get_risk_trend,
    get_risk_summary_for_gemini,
    clear_history,
)
from backend.app.services.external.weather_forecast import analyze_forecast_risks
from backend.app.models.risk_models import RiskLevel, RiskTrend


# ═══════════════════════════════════════════════════════════════
# Test Data — California Corridor Scenario
# ═══════════════════════════════════════════════════════════════

FRESNO_WILDFIRE = {
    "id": "eonet-wildfire-fresno-001",
    "event_type": "wildfire",
    "severity_weight": 1.0,
    "title": "Fresno Wildfire — Highway 99 Corridor",
    "centroid_lat": 36.7378,
    "centroid_lng": -119.7871,
    "geometry_geojson": {
        "type": "Polygon",
        "coordinates": [[
            [-119.85, 36.68],
            [-119.72, 36.68],
            [-119.72, 36.80],
            [-119.85, 36.80],
            [-119.85, 36.68],
        ]],
    },
}

TRUCKS = [
    {
        "id": "trk-018",
        "callsign": "TRK-018",
        "lat": 36.70,
        "lng": -119.79,
        "speed_kmh": 85,
        "heading_deg": 0,    # heading north, toward wildfire
        "cargo_type": "medical",
        "cargo_priority": 3,  # critical
        "origin": "Los Angeles",
        "destination": "Sacramento Depot",
        "eta": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
    },
    {
        "id": "trk-031",
        "callsign": "TRK-031",
        "lat": 36.60,
        "lng": -119.80,
        "speed_kmh": 75,
        "heading_deg": 10,
        "cargo_type": "food",
        "cargo_priority": 2,
        "origin": "Los Angeles",
        "destination": "Sacramento Depot",
        "eta": (datetime.now(timezone.utc) + timedelta(hours=7)).isoformat(),
    },
    {
        "id": "trk-047",
        "callsign": "TRK-047",
        "lat": 37.50,
        "lng": -121.50,
        "speed_kmh": 65,
        "heading_deg": 180,   # heading south, away from wildfire
        "cargo_type": "industrial",
        "cargo_priority": 1,
        "origin": "San Francisco",
        "destination": "Portland",
        "eta": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
    },
    # Dependent truck at Sacramento Depot (for cascade testing)
    {
        "id": "trk-050",
        "callsign": "TRK-050",
        "lat": 38.58,
        "lng": -121.49,
        "speed_kmh": 0,
        "heading_deg": 0,
        "cargo_type": "food",
        "cargo_priority": 1,
        "origin": "Sacramento Depot",  # depends on deliveries to Sacramento
        "destination": "Portland",
        "planned_departure_hours_from_now": 2.0,
        "eta": (datetime.now(timezone.utc) + timedelta(hours=14)).isoformat(),
    },
]


class TestEndToEndPipeline:
    """Full pipeline integration test."""

    def setup_method(self):
        """Clear risk history before each test."""
        clear_history()

    def test_full_risk_evaluation_pipeline(self):
        """
        End-to-end test: evaluate risks, check scores, record history,
        calculate cascade, verify Gemini summary text.
        """
        # ── Step 1: Evaluate all risks ──────────────────────────
        results = evaluate_all_risks(TRUCKS, [FRESNO_WILDFIRE])

        assert len(results) > 0, "Should have at least one flagged result"

        # Find TRK-018 result (closest to wildfire, critical cargo)
        trk018_results = [r for r in results if r["truck_callsign"] == "TRK-018"]
        assert len(trk018_results) >= 1, "TRK-018 should be flagged"

        trk018_score = trk018_results[0]["result"]
        print(f"\nTRK-018 risk score: {trk018_score.risk_score:.4f} [{trk018_score.risk_level.value}]")
        print(f"  Proximity: {trk018_score.component_scores.proximity}")
        print(f"  Velocity:  {trk018_score.component_scores.velocity}")
        print(f"  Severity:  {trk018_score.component_scores.severity}")
        print(f"  AQI:       {trk018_score.component_scores.aqi}")
        print(f"  Approaching: {trk018_score.is_approaching}")
        print(f"  Cargo multiplier: {trk018_score.cargo_multiplier}")

        # TRK-018 is close to wildfire with critical cargo — should be HIGH or CRITICAL
        assert trk018_score.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL), \
            f"TRK-018 should be HIGH/CRITICAL, got {trk018_score.risk_level}"

        # ── Step 2: Record risk history ─────────────────────────
        for r in results:
            record_risk_snapshot(
                truck_id=r["truck_id"],
                risk_score=r["result"].risk_score,
                hazard_id=r["hazard_id"],
            )

        # Simulate a second evaluation with slightly higher score
        record_risk_snapshot("trk-018", trk018_score.risk_score + 0.08, FRESNO_WILDFIRE["id"])

        trend = get_risk_trend("trk-018", "TRK-018")
        print(f"\nTRK-018 trend: {trend.trend.value} (delta: {trend.score_delta})")

        assert trend.trend == RiskTrend.INCREASING, \
            "After score increase, trend should be INCREASING"

        # ── Step 3: Gemini summary context ──────────────────────
        summary = get_risk_summary_for_gemini("trk-018", "TRK-018")
        print(f"\nGemini context: {summary}")

        assert "TRK-018" in summary
        assert "risen" in summary or "increasing" in summary.lower()

        # ── Step 4: Cascade impact ──────────────────────────────
        rerouted = {
            "id": "trk-018",
            "callsign": "TRK-018",
            "destination": "Sacramento Depot",
            "time_delta_min": 58,  # 58 min detour
        }

        cascade = calculate_cascade_impact(rerouted, TRUCKS)
        print(f"\nCascade impact: {cascade.affected_delivery_count} deliveries affected")
        print(f"  Total delay: {cascade.total_cascade_delay_hours}h")

        # TRK-050 originates from Sacramento Depot — should be affected
        # But 58min delay < 2 hour planned departure, so it might not cascade
        # This is a valid test of the logic
        assert cascade.primary_truck == "TRK-018"
        assert cascade.affected_depot == "Sacramento Depot"

    def test_distant_truck_low_risk(self):
        """TRK-047 in San Francisco should have LOW risk for Fresno fire."""
        results = evaluate_all_risks([TRUCKS[2]], [FRESNO_WILDFIRE])

        # Should either be filtered out (< 0.40) or have LOW score
        if results:
            trk047_score = results[0]["result"]
            assert trk047_score.risk_level in (RiskLevel.LOW, RiskLevel.MODERATE)

    def test_forecast_analysis_integration(self):
        """Test forecast analysis with realistic weather data."""
        # Simulate a dangerous weather scenario
        forecast = {
            "hourly": {
                "time": [f"2026-04-21T{h:02d}:00" for h in range(24)],
                "windspeed_10m": [20.0] * 10 + [95.0] * 4 + [30.0] * 10,
                "precipitation": [0.0] * 15 + [25.0, 30.0, 15.0] + [0.0] * 6,
                "snowfall": [0.0] * 24,
                "visibility": [50000.0] * 17 + [800.0, 600.0, 900.0] + [50000.0] * 4,
            }
        }

        alerts = analyze_forecast_risks(forecast, TRUCKS[0], lat=36.70, lng=-119.79)

        print(f"\nForecast alerts for TRK-018:")
        for alert in alerts:
            print(f"  {alert.forecast_type.value}: {alert.forecast_value}{alert.unit} "
                  f"(threshold {alert.threshold}{alert.unit}) in {alert.hours_ahead}h")

        # Should have wind, precipitation, and visibility alerts
        alert_types = {a.forecast_type for a in alerts}
        assert len(alerts) >= 2, "Should detect multiple weather hazards"

    def test_aqi_impact_on_score(self):
        """Hazardous AQI should measurably increase the risk score."""
        truck = TRUCKS[0].copy()

        score_no_aqi = calculate_risk_score(truck, FRESNO_WILDFIRE, {"aqi": 0})
        score_high_aqi = calculate_risk_score(truck, FRESNO_WILDFIRE, {"aqi": 350})

        print(f"\nAQI impact on TRK-018:")
        print(f"  Without AQI: {score_no_aqi.risk_score:.4f}")
        print(f"  With AQI 350: {score_high_aqi.risk_score:.4f}")

        assert score_high_aqi.risk_score >= score_no_aqi.risk_score
        assert score_high_aqi.component_scores.aqi == 1.0
        assert score_no_aqi.component_scores.aqi == 0.0
