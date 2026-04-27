"""
Risk History & Trend Tracker (Bonus Feature).

Tracks how each truck's risk score changes over recent evaluations,
enabling "Risk is INCREASING" vs "Risk is STABLE" indicators.

This adds demo storytelling value:
  "TRK-018's risk has risen from 0.42 to 0.81 in the last 5 minutes"

Uses in-memory storage (no DB dependency) with a configurable
window size. Each truck keeps the last N risk snapshots.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from ..models.risk_models import (
    RiskSnapshot,
    RiskTrend,
    RiskTrendData,
)

logger = logging.getLogger(__name__)

# Maximum number of risk snapshots to keep per truck
MAX_HISTORY_SIZE = 20

# Threshold for trend detection (minimum score delta to be "increasing" or "decreasing")
TREND_DELTA_THRESHOLD = 0.05

# In-memory storage: truck_id → deque of (risk_score, timestamp, hazard_id)
_risk_history: Dict[str, Deque[RiskSnapshot]] = defaultdict(
    lambda: deque(maxlen=MAX_HISTORY_SIZE)
)


def record_risk_snapshot(
    truck_id: str,
    risk_score: float,
    hazard_id: str,
    timestamp: Optional[datetime] = None,
) -> None:
    """
    Record a single risk evaluation snapshot for a truck.

    Called by the risk scheduler after each evaluation cycle.

    Args:
        truck_id: UUID or ID of the truck
        risk_score: The calculated risk score (0.0-1.0)
        hazard_id: UUID of the hazard that produced this score
        timestamp: When this was calculated (defaults to now)
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    snapshot = RiskSnapshot(
        risk_score=risk_score,
        timestamp=timestamp,
        hazard_id=hazard_id,
    )

    _risk_history[truck_id].append(snapshot)


def get_risk_trend(
    truck_id: str,
    callsign: Optional[str] = None,
) -> RiskTrendData:
    """
    Get the risk trend for a truck based on recent history.

    Trend logic:
    - INCREASING: latest score > earliest score by > TREND_DELTA_THRESHOLD
    - DECREASING: latest score < earliest score by > TREND_DELTA_THRESHOLD
    - STABLE: score hasn't changed significantly

    Args:
        truck_id: UUID or ID of the truck
        callsign: Optional callsign for display

    Returns:
        RiskTrendData with current score, trend direction, and history.
    """
    history = _risk_history.get(truck_id, deque())

    if len(history) == 0:
        return RiskTrendData(
            truck_id=truck_id,
            callsign=callsign,
            current_score=0.0,
            trend=RiskTrend.STABLE,
            evaluation_count=0,
        )

    snapshots = list(history)
    current = snapshots[-1]

    if len(snapshots) == 1:
        return RiskTrendData(
            truck_id=truck_id,
            callsign=callsign,
            current_score=current.risk_score,
            trend=RiskTrend.STABLE,
            history=snapshots,
            evaluation_count=1,
        )

    # Compare to the score from ~3 evaluations ago (or earliest)
    comparison_idx = max(0, len(snapshots) - 4)
    previous = snapshots[comparison_idx]

    delta = current.risk_score - previous.risk_score

    if delta > TREND_DELTA_THRESHOLD:
        trend = RiskTrend.INCREASING
    elif delta < -TREND_DELTA_THRESHOLD:
        trend = RiskTrend.DECREASING
    else:
        trend = RiskTrend.STABLE

    return RiskTrendData(
        truck_id=truck_id,
        callsign=callsign,
        current_score=current.risk_score,
        previous_score=previous.risk_score,
        trend=trend,
        score_delta=round(delta, 4),
        history=snapshots,
        evaluation_count=len(snapshots),
    )


def get_all_trends() -> Dict[str, RiskTrendData]:
    """Get risk trends for all tracked trucks."""
    return {
        truck_id: get_risk_trend(truck_id)
        for truck_id in _risk_history
    }


def get_risk_summary_for_gemini(truck_id: str, callsign: str = "") -> str:
    """
    Generate a human-readable risk trend summary for Gemini context injection.

    This is used by Person 4's Gemini integration to add context like:
    "TRK-018's risk has risen from 0.42 to 0.81 over the last 5 evaluations"

    Args:
        truck_id: UUID or ID of the truck
        callsign: Truck callsign for the summary text

    Returns:
        A natural-language summary string.
    """
    trend_data = get_risk_trend(truck_id, callsign)
    name = callsign or truck_id[:8]

    if trend_data.evaluation_count == 0:
        return f"{name} has no risk evaluation history."

    if trend_data.evaluation_count == 1:
        return f"{name} risk score: {trend_data.current_score:.2f} (first evaluation)."

    direction = trend_data.trend.value.lower()
    prev = trend_data.previous_score or 0
    curr = trend_data.current_score

    if trend_data.trend == RiskTrend.INCREASING:
        return (
            f"{name}'s risk has risen from {prev:.2f} to {curr:.2f} "
            f"over the last {trend_data.evaluation_count} evaluations "
            f"(+{trend_data.score_delta:.2f}). Risk is {direction}."
        )
    elif trend_data.trend == RiskTrend.DECREASING:
        return (
            f"{name}'s risk has dropped from {prev:.2f} to {curr:.2f} "
            f"over the last {trend_data.evaluation_count} evaluations "
            f"({trend_data.score_delta:.2f}). Risk is {direction}."
        )
    else:
        return (
            f"{name}'s risk score is stable at {curr:.2f} "
            f"over the last {trend_data.evaluation_count} evaluations."
        )


def clear_history(truck_id: Optional[str] = None) -> None:
    """
    Clear risk history.

    Args:
        truck_id: If provided, clear only that truck's history.
                  If None, clear all history.
    """
    if truck_id:
        _risk_history.pop(truck_id, None)
        logger.debug("Cleared risk history for truck %s", truck_id)
    else:
        _risk_history.clear()
        logger.debug("Cleared all risk history")
