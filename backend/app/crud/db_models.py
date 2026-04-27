"""SQLAlchemy ORM models — owned by Person 1.

Placed in crud/ to avoid touching Person 2's app/models/ directory.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Truck(Base):
    __tablename__ = "trucks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    callsign: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    speed_kmh: Mapped[float] = mapped_column(Float, default=0.0)
    heading_deg: Mapped[float] = mapped_column(Float, default=0.0)
    cargo_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cargo_priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")
    origin: Mapped[str | None] = mapped_column(String(256), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(256), nullable=True)
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    route_polyline: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HazardZone(Base):
    __tablename__ = "hazard_zones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_api: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    severity_weight: Mapped[float] = mapped_column(Float, default=0.5)
    geometry_geojson: Mapped[dict] = mapped_column(JSON, nullable=True)
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lng: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    truck_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hazard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hazard_zones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    proximity_km: Mapped[float] = mapped_column(Float, nullable=False)
    eta_to_hazard_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    component_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ForecastAlert(Base):
    __tablename__ = "forecast_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    truck_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    forecast_type: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    hours_ahead: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Country(Base):
    __tablename__ = "countries"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    risk_tier: Mapped[str] = mapped_column(String(32), default="medium")
    customs_complexity: Mapped[float] = mapped_column(Float, default=0.3)


class Port(Base):
    __tablename__ = "ports"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), ForeignKey("countries.code"), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    port_type: Mapped[str] = mapped_column(String(64), default="seaport")
    annual_teu_m: Mapped[float] = mapped_column(Float, default=0.0)


class LogisticsLane(Base):
    __tablename__ = "logistics_lanes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(256), nullable=False)
    destination: Mapped[str] = mapped_column(String(256), nullable=False)
    origin_country: Mapped[str] = mapped_column(String(2), ForeignKey("countries.code"), nullable=False)
    destination_country: Mapped[str] = mapped_column(String(2), ForeignKey("countries.code"), nullable=False)
    flow_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    incoterm: Mapped[str] = mapped_column(String(16), nullable=False)
    service_level: Mapped[str] = mapped_column(String(64), nullable=False)
    port_codes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    route_geojson: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    base_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    customs_buffer_hours: Mapped[float] = mapped_column(Float, default=0.0)
    carbon_kg: Mapped[float] = mapped_column(Float, default=0.0)
    reliability: Mapped[float] = mapped_column(Float, default=0.8)


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    lane_id: Mapped[str] = mapped_column(String(64), ForeignKey("logistics_lanes.id"), nullable=False, index=True)
    cargo_type: Mapped[str] = mapped_column(String(128), nullable=False)
    cargo_priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="ON_ROUTE", index=True)
    current_lat: Mapped[float] = mapped_column(Float, nullable=False)
    current_lng: Mapped[float] = mapped_column(Float, nullable=False)
    projected_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    predicted_delay_min: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RoutePlan(Base):
    __tablename__ = "route_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    objective_score: Mapped[float] = mapped_column(Float, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    duration_min: Mapped[float] = mapped_column(Float, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    risk_index: Mapped[float] = mapped_column(Float, nullable=False)
    route_geojson: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    condition_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
