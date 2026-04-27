"""
Unit tests for the Cascade Impact Engine.

Tests cascade delay propagation for:
- No downstream dependencies
- Single dependent truck
- Multiple dependent trucks
- Edge cases (no delay, no destination, etc.)
"""

import pytest
from datetime import datetime, timezone, timedelta

from backend.app.services.cascade_engine import (
    calculate_cascade_impact,
    calculate_multi_truck_cascade,
)
from backend.app.models.risk_models import CascadeImpactResult


# ═══════════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════════

def make_rerouted_truck(
    truck_id="TRK-018-id",
    callsign="TRK-018",
    destination="Sacramento Depot",
    time_delta_min=252,  # 4.2 hours delay
):
    return {
        "id": truck_id,
        "callsign": callsign,
        "destination": destination,
        "time_delta_min": time_delta_min,
    }


def make_dependent_truck(
    truck_id,
    callsign,
    origin,
    destination,
    planned_departure_hours=2.0,
    eta_hours_from_now=8.0,
):
    now = datetime.now(timezone.utc)
    return {
        "id": truck_id,
        "callsign": callsign,
        "origin": origin,
        "destination": destination,
        "planned_departure_hours_from_now": planned_departure_hours,
        "eta": (now + timedelta(hours=eta_hours_from_now)).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# Basic Cascade Tests
# ═══════════════════════════════════════════════════════════════

class TestCascadeBasic:
    """Basic cascade impact calculations."""

    def test_no_dependent_trucks(self):
        """No trucks originate from the destination → no cascade."""
        rerouted = make_rerouted_truck()
        all_trucks = [
            make_dependent_truck("t1", "TRK-001", "Los Angeles", "Portland"),
            make_dependent_truck("t2", "TRK-002", "San Francisco", "Seattle"),
        ]

        result = calculate_cascade_impact(rerouted, all_trucks)

        assert result.affected_delivery_count == 0
        assert result.total_cascade_delay_hours == 0.0
        assert result.affected_deliveries == []

    def test_single_dependent_truck(self):
        """One truck waiting at Sacramento Depot → cascade delay."""
        rerouted = make_rerouted_truck(time_delta_min=240)  # 4 hours delay
        all_trucks = [
            make_dependent_truck(
                "t1", "TRK-031", "Sacramento Depot", "Portland",
                planned_departure_hours=1.0,  # departs in 1 hour
            ),
        ]

        result = calculate_cascade_impact(rerouted, all_trucks)

        assert result.affected_delivery_count == 1
        assert result.affected_deliveries[0].callsign == "TRK-031"
        # 4h delay - 1h planned departure = 3h cascade delay
        assert result.affected_deliveries[0].cascade_delay_hours == 3.0
        assert result.total_cascade_delay_hours == 3.0

    def test_multiple_dependent_trucks(self):
        """Multiple trucks at the depot → multiple cascades."""
        rerouted = make_rerouted_truck(time_delta_min=300)  # 5 hours delay
        all_trucks = [
            make_dependent_truck(
                "t1", "TRK-031", "Sacramento Depot", "Portland",
                planned_departure_hours=1.0,
            ),
            make_dependent_truck(
                "t2", "TRK-047", "Sacramento Depot", "Seattle",
                planned_departure_hours=3.0,
            ),
            make_dependent_truck(
                "t3", "TRK-099", "Los Angeles", "Phoenix",  # different origin
                planned_departure_hours=1.0,
            ),
        ]

        result = calculate_cascade_impact(rerouted, all_trucks)

        assert result.affected_delivery_count == 2  # only the 2 at Sacramento
        assert result.primary_truck == "TRK-018"
        assert result.affected_depot == "Sacramento Depot"

    def test_no_delay(self):
        """Zero delay → no cascade impact."""
        rerouted = make_rerouted_truck(time_delta_min=0)
        all_trucks = [
            make_dependent_truck(
                "t1", "TRK-031", "Sacramento Depot", "Portland",
                planned_departure_hours=1.0,
            ),
        ]

        result = calculate_cascade_impact(rerouted, all_trucks)
        assert result.affected_delivery_count == 0

    def test_departure_after_delay(self):
        """Truck departing after delay has passed → no cascade."""
        rerouted = make_rerouted_truck(time_delta_min=60)  # 1 hour delay
        all_trucks = [
            make_dependent_truck(
                "t1", "TRK-031", "Sacramento Depot", "Portland",
                planned_departure_hours=3.0,  # departs in 3 hours (after 1h delay absorbed)
            ),
        ]

        result = calculate_cascade_impact(rerouted, all_trucks)
        assert result.affected_delivery_count == 0

    def test_self_exclusion(self):
        """Rerouted truck should not be counted as its own dependent."""
        rerouted = make_rerouted_truck(truck_id="TRK-018-id")
        all_trucks = [
            {
                "id": "TRK-018-id",
                "callsign": "TRK-018",
                "origin": "Sacramento Depot",
                "destination": "Portland",
                "planned_departure_hours_from_now": 0.5,
                "eta": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
            },
        ]

        result = calculate_cascade_impact(rerouted, all_trucks)
        assert result.affected_delivery_count == 0


# ═══════════════════════════════════════════════════════════════
# Multi-Truck Cascade Tests
# ═══════════════════════════════════════════════════════════════

class TestMultiTruckCascade:
    """Tests for calculating cascades from multiple rerouted trucks."""

    def test_multi_truck_cascade(self):
        """Multiple rerouted trucks should produce separate cascade results."""
        rerouted_trucks = [
            make_rerouted_truck(truck_id="t1", callsign="TRK-018", time_delta_min=240),
            make_rerouted_truck(truck_id="t2", callsign="TRK-031", destination="Portland Depot", time_delta_min=120),
        ]
        all_trucks = [
            make_dependent_truck("t3", "TRK-050", "Sacramento Depot", "Phoenix", planned_departure_hours=1.0),
            make_dependent_truck("t4", "TRK-060", "Portland Depot", "Seattle", planned_departure_hours=0.5),
        ]

        results = calculate_multi_truck_cascade(rerouted_trucks, all_trucks)

        assert len(results) == 2
        assert "t1" in results
        assert "t2" in results


# ═══════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════

class TestCascadeEdgeCases:
    """Edge case tests for cascade engine."""

    def test_no_destination(self):
        """Missing destination should return empty cascade."""
        rerouted = make_rerouted_truck(destination="")
        result = calculate_cascade_impact(rerouted, [])
        assert result.affected_delivery_count == 0

    def test_empty_all_trucks(self):
        """Empty truck list → no cascade."""
        rerouted = make_rerouted_truck()
        result = calculate_cascade_impact(rerouted, [])
        assert result.affected_delivery_count == 0

    def test_depot_validation(self):
        """With depots list, non-existent depot should return empty cascade."""
        rerouted = make_rerouted_truck(destination="Nonexistent Depot")
        depots = [{"name": "Sacramento Depot"}, {"name": "Portland Depot"}]
        all_trucks = [
            make_dependent_truck("t1", "TRK-031", "Nonexistent Depot", "Portland"),
        ]

        result = calculate_cascade_impact(rerouted, all_trucks, depots)
        assert result.affected_delivery_count == 0
