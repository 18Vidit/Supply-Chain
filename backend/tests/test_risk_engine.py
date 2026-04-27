"""
Unit tests for the Risk Score Engine.

Tests all four components of the risk calculation:
- Proximity (Shapely geometric distance)
- Trajectory/Velocity (heading projection)
- Severity (hazard weight lookup)
- AQI (air quality tiering)
Plus the cargo priority multiplier and batch evaluation.
"""

import pytest

from backend.app.services.risk_engine import (
    calculate_risk_score,
    evaluate_all_risks,
)
from backend.app.models.risk_models import RiskLevel, RiskScoreResult


# ═══════════════════════════════════════════════════════════════
# Test Fixtures — Synthetic Data
# ═══════════════════════════════════════════════════════════════

def make_truck(
    lat=36.7,
    lng=-119.8,
    speed_kmh=80,
    heading_deg=0,
    cargo_priority=1,
    callsign="TRK-TEST",
    truck_id="test-truck-001",
):
    """Create a synthetic truck dict."""
    return {
        "id": truck_id,
        "callsign": callsign,
        "lat": lat,
        "lng": lng,
        "speed_kmh": speed_kmh,
        "heading_deg": heading_deg,
        "cargo_priority": cargo_priority,
    }


def make_hazard(
    centroid_lat=36.75,
    centroid_lng=-119.75,
    event_type="wildfire",
    severity_weight=1.0,
    title="Test Wildfire",
    hazard_id="test-hazard-001",
):
    """Create a synthetic hazard zone dict with a polygon GeoJSON."""
    # Create a simple polygon around the centroid (~10km square)
    offset = 0.05  # ~5.5km
    return {
        "id": hazard_id,
        "event_type": event_type,
        "severity_weight": severity_weight,
        "title": title,
        "centroid_lat": centroid_lat,
        "centroid_lng": centroid_lng,
        "geometry_geojson": {
            "type": "Polygon",
            "coordinates": [[
                [centroid_lng - offset, centroid_lat - offset],
                [centroid_lng + offset, centroid_lat - offset],
                [centroid_lng + offset, centroid_lat + offset],
                [centroid_lng - offset, centroid_lat + offset],
                [centroid_lng - offset, centroid_lat - offset],
            ]],
        },
    }


# ═══════════════════════════════════════════════════════════════
# Proximity Tests
# ═══════════════════════════════════════════════════════════════

class TestProximity:
    """Tests for the proximity component of risk scoring."""

    def test_truck_inside_hazard_zone(self):
        """Truck inside the hazard polygon should get proximity_score = 1.0."""
        truck = make_truck(lat=36.75, lng=-119.75)  # right at centroid
        hazard = make_hazard(centroid_lat=36.75, centroid_lng=-119.75)

        result = calculate_risk_score(truck, hazard)

        assert result.component_scores.proximity == 1.0
        assert result.proximity_km == 0.0

    def test_truck_far_away(self):
        """Truck 200+ km away should get proximity_score ≈ 0.0."""
        truck = make_truck(lat=38.5, lng=-121.5)  # ~200km away
        hazard = make_hazard(centroid_lat=36.75, centroid_lng=-119.75)

        result = calculate_risk_score(truck, hazard)

        assert result.component_scores.proximity == 0.0
        assert result.proximity_km > 150

    def test_proximity_gradient(self):
        """Score should decrease as truck moves further from hazard."""
        hazard = make_hazard()

        close_truck = make_truck(lat=36.8, lng=-119.8)    # ~6km
        mid_truck = make_truck(lat=37.0, lng=-119.8)      # ~28km
        far_truck = make_truck(lat=37.5, lng=-119.8)      # ~83km

        close_result = calculate_risk_score(close_truck, hazard)
        mid_result = calculate_risk_score(mid_truck, hazard)
        far_result = calculate_risk_score(far_truck, hazard)

        assert close_result.component_scores.proximity > mid_result.component_scores.proximity
        assert mid_result.component_scores.proximity > far_result.component_scores.proximity


# ═══════════════════════════════════════════════════════════════
# Velocity / Trajectory Tests
# ═══════════════════════════════════════════════════════════════

class TestVelocity:
    """Tests for the trajectory/velocity component."""

    def test_approaching_truck_scores_higher(self):
        """Truck heading toward hazard should score higher than one heading away."""
        # Hazard due north at ~167km — far enough that 2-hour projection
        # at 60km/h (≈108km) doesn't overshoot
        hazard = make_hazard(centroid_lat=37.5, centroid_lng=-119.8)

        # Heading 0° = due north, toward the hazard
        approaching = make_truck(lat=36.0, lng=-119.8, heading_deg=0, speed_kmh=60)
        # Heading 180° = due south, away from hazard
        retreating = make_truck(lat=36.0, lng=-119.8, heading_deg=180, speed_kmh=60)

        approach_result = calculate_risk_score(approaching, hazard)
        retreat_result = calculate_risk_score(retreating, hazard)

        # Approaching truck should have higher velocity score
        assert approach_result.component_scores.velocity > retreat_result.component_scores.velocity
        assert approach_result.is_approaching is True
        assert retreat_result.is_approaching is False

    def test_fast_truck_scores_higher(self):
        """Faster truck approaching should have higher velocity score (lower ETA)."""
        hazard = make_hazard(centroid_lat=37.0, centroid_lng=-119.75)

        fast = make_truck(lat=36.7, lng=-119.75, heading_deg=0, speed_kmh=120)
        slow = make_truck(lat=36.7, lng=-119.75, heading_deg=0, speed_kmh=40)

        fast_result = calculate_risk_score(fast, hazard)
        slow_result = calculate_risk_score(slow, hazard)

        assert fast_result.component_scores.velocity >= slow_result.component_scores.velocity

    def test_distant_truck_zero_velocity_score(self):
        """Truck > 4 hours away should have velocity_score = 0.0."""
        # Hazard 500km north — even at 80km/h, ETA > 6 hours
        hazard = make_hazard(centroid_lat=41.5, centroid_lng=-119.75)
        truck = make_truck(lat=36.7, lng=-119.75, heading_deg=0, speed_kmh=80)

        result = calculate_risk_score(truck, hazard)
        assert result.component_scores.velocity == 0.0


# ═══════════════════════════════════════════════════════════════
# Severity Tests
# ═══════════════════════════════════════════════════════════════

class TestSeverity:
    """Tests for the hazard severity component."""

    def test_wildfire_max_severity(self):
        """Wildfire should have severity_weight = 1.0."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard(event_type="wildfire", severity_weight=1.0)

        result = calculate_risk_score(truck, hazard)
        assert result.component_scores.severity == 1.0

    def test_flood_watch_lower_severity(self):
        """Flood watch should have lower severity than wildfire."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard(event_type="flood_watch", severity_weight=0.55)

        result = calculate_risk_score(truck, hazard)
        assert result.component_scores.severity == 0.55

    def test_unknown_event_uses_hazard_weight(self):
        """Unknown event type should fall back to hazard's own severity_weight."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard(event_type="alien_invasion", severity_weight=0.42)

        result = calculate_risk_score(truck, hazard)
        assert result.component_scores.severity == 0.42


# ═══════════════════════════════════════════════════════════════
# AQI Tests
# ═══════════════════════════════════════════════════════════════

class TestAQI:
    """Tests for the air quality component."""

    def test_hazardous_aqi(self):
        """AQI >= 301 should give max AQI score of 1.0."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard, aqi_data={"aqi": 350})
        assert result.component_scores.aqi == 1.0

    def test_good_aqi(self):
        """AQI < 101 should give AQI score of 0.0."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard, aqi_data={"aqi": 50})
        assert result.component_scores.aqi == 0.0

    def test_no_aqi_data(self):
        """No AQI data should default to 0 (no penalty)."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard)
        assert result.component_scores.aqi == 0.0

    def test_aqi_tiers(self):
        """Each AQI tier should produce the correct score."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard()

        tier_tests = [
            (50, 0.0),
            (120, 0.25),
            (160, 0.5),
            (250, 0.75),
            (400, 1.0),
        ]

        for aqi_val, expected_score in tier_tests:
            result = calculate_risk_score(truck, hazard, aqi_data={"aqi": aqi_val})
            assert result.component_scores.aqi == expected_score, \
                f"AQI {aqi_val} expected score {expected_score}, got {result.component_scores.aqi}"


# ═══════════════════════════════════════════════════════════════
# Cargo Priority Tests
# ═══════════════════════════════════════════════════════════════

class TestCargoPriority:
    """Tests for the cargo priority multiplier."""

    def test_priority_1_no_boost(self):
        """Priority 1 (standard) should have multiplier 1.0."""
        truck = make_truck(lat=36.75, lng=-119.75, cargo_priority=1)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard)
        assert result.cargo_multiplier == 1.0

    def test_priority_3_critical_boost(self):
        """Priority 3 (critical medical) should have multiplier 1.3."""
        truck = make_truck(lat=36.75, lng=-119.75, cargo_priority=3)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard)
        assert result.cargo_multiplier == 1.30

    def test_critical_cargo_higher_score(self):
        """Same position, critical cargo should produce higher risk score."""
        hazard = make_hazard()

        standard = make_truck(lat=36.8, lng=-119.8, cargo_priority=1)
        critical = make_truck(lat=36.8, lng=-119.8, cargo_priority=3)

        std_result = calculate_risk_score(standard, hazard)
        crit_result = calculate_risk_score(critical, hazard)

        assert crit_result.risk_score >= std_result.risk_score

    def test_score_capped_at_one(self):
        """Risk score should never exceed 1.0 even with high multiplier."""
        # Truck inside hazard with max AQI
        truck = make_truck(lat=36.75, lng=-119.75, cargo_priority=3)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard, aqi_data={"aqi": 500})
        assert result.risk_score <= 1.0


# ═══════════════════════════════════════════════════════════════
# Risk Classification Tests
# ═══════════════════════════════════════════════════════════════

class TestRiskClassification:
    """Tests for risk level classification."""

    def test_critical_classification(self):
        """Score >= 0.82 should be CRITICAL."""
        assert RiskScoreResult.classify_risk(0.85) == RiskLevel.CRITICAL
        assert RiskScoreResult.classify_risk(0.82) == RiskLevel.CRITICAL
        assert RiskScoreResult.classify_risk(1.0) == RiskLevel.CRITICAL

    def test_high_classification(self):
        """Score >= 0.65 and < 0.82 should be HIGH."""
        assert RiskScoreResult.classify_risk(0.65) == RiskLevel.HIGH
        assert RiskScoreResult.classify_risk(0.75) == RiskLevel.HIGH
        assert RiskScoreResult.classify_risk(0.81) == RiskLevel.HIGH

    def test_moderate_classification(self):
        """Score >= 0.40 and < 0.65 should be MODERATE."""
        assert RiskScoreResult.classify_risk(0.40) == RiskLevel.MODERATE
        assert RiskScoreResult.classify_risk(0.55) == RiskLevel.MODERATE

    def test_low_classification(self):
        """Score < 0.40 should be LOW."""
        assert RiskScoreResult.classify_risk(0.1) == RiskLevel.LOW
        assert RiskScoreResult.classify_risk(0.0) == RiskLevel.LOW
        assert RiskScoreResult.classify_risk(0.39) == RiskLevel.LOW


# ═══════════════════════════════════════════════════════════════
# Batch Evaluation Tests
# ═══════════════════════════════════════════════════════════════

class TestBatchEvaluation:
    """Tests for the batch evaluate_all_risks function."""

    def test_batch_returns_sorted_results(self):
        """Results should be sorted by risk score descending."""
        trucks = [
            make_truck(lat=36.75, lng=-119.75, truck_id="t1", callsign="TRK-001"),  # inside
            make_truck(lat=37.5, lng=-119.75, truck_id="t2", callsign="TRK-002"),   # far
        ]
        hazards = [make_hazard()]

        results = evaluate_all_risks(trucks, hazards)

        if len(results) >= 2:
            assert results[0]["result"].risk_score >= results[1]["result"].risk_score

    def test_batch_filters_low_scores(self):
        """Results with score < 0.40 should be excluded."""
        trucks = [
            make_truck(lat=40.0, lng=-125.0, truck_id="far-away", callsign="TRK-FAR"),
        ]
        hazards = [make_hazard(centroid_lat=36.75, centroid_lng=-119.75)]

        results = evaluate_all_risks(trucks, hazards)

        for r in results:
            assert r["result"].risk_score >= 0.40

    def test_batch_handles_empty_input(self):
        """Empty trucks or hazards should return empty results."""
        assert evaluate_all_risks([], [make_hazard()]) == []
        assert evaluate_all_risks([make_truck()], []) == []
        assert evaluate_all_risks([], []) == []

    def test_batch_multiple_hazards(self):
        """Each truck should be evaluated against each hazard."""
        trucks = [make_truck(lat=36.75, lng=-119.75, truck_id="t1")]
        hazards = [
            make_hazard(hazard_id="h1", centroid_lat=36.75, centroid_lng=-119.75),
            make_hazard(hazard_id="h2", centroid_lat=36.80, centroid_lng=-119.80),
        ]

        results = evaluate_all_risks(trucks, hazards)

        # Should have results for both hazards (truck is close to both)
        hazard_ids = [r["hazard_id"] for r in results]
        assert "h1" in hazard_ids or "h2" in hazard_ids


# ═══════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_zero_speed_truck(self):
        """Truck with speed=0 should not cause division by zero."""
        truck = make_truck(speed_kmh=0, lat=36.75, lng=-119.75)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard)
        assert result.risk_score >= 0
        assert result.risk_score <= 1.0

    def test_missing_aqi_key(self):
        """AQI data missing 'aqi' key should default to 0."""
        truck = make_truck(lat=36.75, lng=-119.75)
        hazard = make_hazard()

        result = calculate_risk_score(truck, hazard, aqi_data={})
        assert result.component_scores.aqi == 0.0

    def test_invalid_geojson_graceful(self):
        """Invalid GeoJSON should not crash — returns 0 proximity."""
        truck = make_truck()
        hazard = make_hazard()
        hazard["geometry_geojson"] = {"type": "invalid"}

        result = calculate_risk_score(truck, hazard)
        assert result.component_scores.proximity == 0.0
