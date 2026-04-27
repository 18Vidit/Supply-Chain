"""
Pydantic models for the Risk Engine + Intelligence Layer.
These define the shape of all data flowing through the risk pipeline.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════

class RiskLevel(str, Enum):
    """Risk classification based on score thresholds."""
    LOW = "LOW"             # score < 0.40
    MODERATE = "MODERATE"   # 0.40 <= score < 0.65
    HIGH = "HIGH"           # 0.65 <= score < 0.82
    CRITICAL = "CRITICAL"   # score >= 0.82


class RiskEventStatus(str, Enum):
    """Lifecycle status of a risk event."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DISMISSED = "DISMISSED"
    AUTO_REROUTED = "AUTO_REROUTED"


class TruckStatus(str, Enum):
    """Truck operational status."""
    ON_ROUTE = "ON_ROUTE"
    REROUTED = "REROUTED"
    DELAYED = "DELAYED"
    STOPPED = "STOPPED"


class ForecastType(str, Enum):
    """Types of weather forecast alerts."""
    HIGH_WIND = "high_wind"
    HEAVY_PRECIPITATION = "heavy_precipitation"
    HEAVY_SNOW = "heavy_snow"
    ZERO_VISIBILITY = "zero_visibility"


class RiskTrend(str, Enum):
    """Direction of risk score change over recent evaluations."""
    INCREASING = "INCREASING"
    STABLE = "STABLE"
    DECREASING = "DECREASING"


# ═══════════════════════════════════════════════════════════════
# Core Risk Calculation Models
# ═══════════════════════════════════════════════════════════════

class ComponentScores(BaseModel):
    """Breakdown of the four risk score components."""
    proximity: float = Field(..., ge=0.0, le=1.0, description="Proximity to hazard boundary (35% weight)")
    velocity: float = Field(..., ge=0.0, le=1.0, description="Trajectory/velocity factor (30% weight)")
    severity: float = Field(..., ge=0.0, le=1.0, description="Hazard severity weight (25% weight)")
    aqi: float = Field(..., ge=0.0, le=1.0, description="Air quality index factor (10% weight)")


class RiskScoreResult(BaseModel):
    """Output of the risk score calculation for a truck-hazard pair."""
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Final weighted risk score (0.0-1.0)")
    risk_level: RiskLevel = Field(..., description="Classified risk level")
    eta_to_hazard_min: int = Field(..., description="Estimated time to reach hazard zone (minutes)")
    proximity_km: float = Field(..., ge=0.0, description="Current distance to hazard boundary (km)")
    component_scores: ComponentScores
    is_approaching: bool = Field(..., description="Whether truck is moving toward the hazard")
    raw_score: float = Field(..., description="Score before cargo priority multiplier")
    cargo_multiplier: float = Field(..., description="Applied cargo priority multiplier")

    @staticmethod
    def classify_risk(score: float) -> RiskLevel:
        """Classify a risk score into a risk level."""
        if score >= 0.82:
            return RiskLevel.CRITICAL
        elif score >= 0.65:
            return RiskLevel.HIGH
        elif score >= 0.40:
            return RiskLevel.MODERATE
        else:
            return RiskLevel.LOW


# ═══════════════════════════════════════════════════════════════
# Cascade Impact Models
# ═══════════════════════════════════════════════════════════════

class AffectedDelivery(BaseModel):
    """A downstream delivery affected by a truck's reroute delay."""
    truck_id: str
    callsign: str
    destination: str
    cascade_delay_hours: float
    new_eta: str  # ISO format datetime string


class CascadeImpactResult(BaseModel):
    """Output of the cascade impact calculation."""
    primary_truck: str = Field(..., description="Callsign of the rerouted truck")
    affected_depot: str = Field(..., description="Depot that receives delayed cargo")
    affected_deliveries: List[AffectedDelivery] = Field(default_factory=list)
    total_cascade_delay_hours: float = Field(default=0.0)
    affected_delivery_count: int = Field(default=0)


# ═══════════════════════════════════════════════════════════════
# Forecast Alert Models
# ═══════════════════════════════════════════════════════════════

class ForecastAlertData(BaseModel):
    """A proactive weather forecast alert for a truck's route."""
    truck_id: Optional[str] = None
    forecast_type: ForecastType
    forecast_value: float = Field(..., description="Actual forecasted value")
    threshold: float = Field(..., description="Threshold that was exceeded")
    unit: str = Field(..., description="Unit of measurement")
    hours_ahead: int = Field(..., description="Hours until this condition hits")
    lat: float
    lng: float
    is_active: bool = True
    created_at: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════
# AQI Models
# ═══════════════════════════════════════════════════════════════

class AQIData(BaseModel):
    """Air quality index data from AirNow."""
    aqi: int = Field(default=0, ge=0, description="Current AQI value")
    category: str = Field(default="Unknown", description="AQI category name")
    parameter: str = Field(default="PM2.5", description="Measured pollutant")
    lat: Optional[float] = None
    lng: Optional[float] = None
    reporting_area: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# Risk Event Models (for API responses)
# ═══════════════════════════════════════════════════════════════

class RiskEventCreate(BaseModel):
    """Data needed to create a new risk event record."""
    truck_id: str
    hazard_id: str
    risk_score: float
    proximity_km: float
    eta_to_hazard_min: int
    component_scores: Dict[str, float]
    status: RiskEventStatus = RiskEventStatus.PENDING
    suggested_route: Optional[Dict[str, Any]] = None
    original_eta: Optional[datetime] = None
    rerouted_eta: Optional[datetime] = None
    time_delta_min: Optional[int] = None
    distance_delta_km: Optional[float] = None
    cascade_impact: Optional[Dict[str, Any]] = None
    gemini_summary: Optional[str] = None


class RiskEventResponse(BaseModel):
    """API response for a risk event."""
    id: str
    truck_id: str
    truck_callsign: Optional[str] = None
    hazard_id: str
    hazard_title: Optional[str] = None
    risk_score: float
    risk_level: RiskLevel
    proximity_km: float
    eta_to_hazard_min: int
    component_scores: Dict[str, float]
    status: RiskEventStatus
    suggested_route: Optional[Dict[str, Any]] = None
    time_delta_min: Optional[int] = None
    distance_delta_km: Optional[float] = None
    cascade_impact: Optional[CascadeImpactResult] = None
    gemini_summary: Optional[str] = None
    risk_trend: Optional[RiskTrend] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════
# Dashboard Statistics
# ═══════════════════════════════════════════════════════════════

class RiskDashboardStats(BaseModel):
    """Summary statistics for the risk dashboard."""
    total_active_events: int = 0
    critical_count: int = 0
    high_count: int = 0
    moderate_count: int = 0
    pending_count: int = 0
    approved_count: int = 0
    auto_rerouted_count: int = 0
    dismissed_count: int = 0
    avg_risk_score: float = 0.0
    max_risk_score: float = 0.0
    trucks_at_risk: int = 0
    active_forecast_alerts: int = 0
    total_cascade_delay_hours: float = 0.0


# ═══════════════════════════════════════════════════════════════
# Risk History / Trend Models (Bonus)
# ═══════════════════════════════════════════════════════════════

class RiskSnapshot(BaseModel):
    """A single point-in-time risk evaluation."""
    risk_score: float
    timestamp: datetime
    hazard_id: str


class RiskTrendData(BaseModel):
    """Risk trend information for a truck over recent evaluations."""
    truck_id: str
    callsign: Optional[str] = None
    current_score: float
    previous_score: Optional[float] = None
    trend: RiskTrend
    score_delta: float = 0.0
    history: List[RiskSnapshot] = Field(default_factory=list)
    evaluation_count: int = 0
