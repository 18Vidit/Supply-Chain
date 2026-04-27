"""global_logistics_tables

Revision ID: 9b2a6df0
Revises: 363860405da4
Create Date: 2026-04-28 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9b2a6df0"
down_revision: Union[str, None] = "363860405da4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "countries",
        sa.Column("code", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("region", sa.String(length=128), nullable=False),
        sa.Column("risk_tier", sa.String(length=32), nullable=False),
        sa.Column("customs_complexity", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index(op.f("ix_countries_region"), "countries", ["region"], unique=False)

    op.create_table(
        "ports",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("city", sa.String(length=128), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("port_type", sa.String(length=64), nullable=False),
        sa.Column("annual_teu_m", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["country_code"], ["countries.code"]),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index(op.f("ix_ports_country_code"), "ports", ["country_code"], unique=False)

    op.create_table(
        "logistics_lanes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("origin", sa.String(length=256), nullable=False),
        sa.Column("destination", sa.String(length=256), nullable=False),
        sa.Column("origin_country", sa.String(length=2), nullable=False),
        sa.Column("destination_country", sa.String(length=2), nullable=False),
        sa.Column("flow_type", sa.String(length=32), nullable=False),
        sa.Column("incoterm", sa.String(length=16), nullable=False),
        sa.Column("service_level", sa.String(length=64), nullable=False),
        sa.Column("port_codes", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("route_geojson", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("base_cost_usd", sa.Float(), nullable=False),
        sa.Column("customs_buffer_hours", sa.Float(), nullable=False),
        sa.Column("carbon_kg", sa.Float(), nullable=False),
        sa.Column("reliability", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["destination_country"], ["countries.code"]),
        sa.ForeignKeyConstraint(["origin_country"], ["countries.code"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_logistics_lanes_flow_type"), "logistics_lanes", ["flow_type"], unique=False)
    op.create_index(op.f("ix_logistics_lanes_mode"), "logistics_lanes", ["mode"], unique=False)

    op.create_table(
        "shipments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("external_ref", sa.String(length=64), nullable=False),
        sa.Column("lane_id", sa.String(length=64), nullable=False),
        sa.Column("cargo_type", sa.String(length=128), nullable=False),
        sa.Column("cargo_priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_lat", sa.Float(), nullable=False),
        sa.Column("current_lng", sa.Float(), nullable=False),
        sa.Column("projected_cost_usd", sa.Float(), nullable=False),
        sa.Column("predicted_delay_min", sa.Float(), nullable=False),
        sa.Column("metadata_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lane_id"], ["logistics_lanes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shipments_external_ref"), "shipments", ["external_ref"], unique=True)
    op.create_index(op.f("ix_shipments_lane_id"), "shipments", ["lane_id"], unique=False)
    op.create_index(op.f("ix_shipments_status"), "shipments", ["status"], unique=False)

    op.create_table(
        "route_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("shipment_id", sa.UUID(), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("objective_score", sa.Float(), nullable=False),
        sa.Column("distance_km", sa.Float(), nullable=False),
        sa.Column("duration_min", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("risk_index", sa.Float(), nullable=False),
        sa.Column("route_geojson", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("condition_snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_route_plans_shipment_id"), "route_plans", ["shipment_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_route_plans_shipment_id"), table_name="route_plans")
    op.drop_table("route_plans")
    op.drop_index(op.f("ix_shipments_status"), table_name="shipments")
    op.drop_index(op.f("ix_shipments_lane_id"), table_name="shipments")
    op.drop_index(op.f("ix_shipments_external_ref"), table_name="shipments")
    op.drop_table("shipments")
    op.drop_index(op.f("ix_logistics_lanes_mode"), table_name="logistics_lanes")
    op.drop_index(op.f("ix_logistics_lanes_flow_type"), table_name="logistics_lanes")
    op.drop_table("logistics_lanes")
    op.drop_index(op.f("ix_ports_country_code"), table_name="ports")
    op.drop_table("ports")
    op.drop_index(op.f("ix_countries_region"), table_name="countries")
    op.drop_table("countries")
