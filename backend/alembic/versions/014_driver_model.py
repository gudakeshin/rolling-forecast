"""Causal driver tables + can_manage_drivers permission.

Revision ID: 014_driver_model
Revises: 013_selection_metrics
Create Date: 2026-07-28
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import (  # noqa: E402
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
    drop_column_if_exists,
    drop_table_if_exists,
)

revision: str = "014_driver_model"
down_revision: Union[str, None] = "013_selection_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "roles",
        sa.Column("can_manage_drivers", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Seed admin + analyst (and reviewer/publisher who already can_override)
    bind = op.get_bind()
    try:
        bind.execute(
            text(
                "UPDATE roles SET can_manage_drivers = 1 "
                "WHERE name IN ('admin', 'analyst', 'reviewer', 'publisher')"
            )
        )
    except Exception:
        pass

    create_table_if_missing(
        "drivers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("driver_type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("aggregation", sa.String(32), nullable=False, server_default="sum"),
        sa.Column("business_unit", sa.String(100), nullable=True),
        sa.Column("geography", sa.String(100), nullable=True),
        sa.Column("product_line", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("key", name="uq_drivers_key"),
    )
    create_index_if_missing("ix_drivers_business_unit", "drivers", ["business_unit"])
    create_index_if_missing("ix_drivers_driver_type", "drivers", ["driver_type"])

    create_table_if_missing(
        "driver_discovery_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("line_item_id", sa.Integer(), sa.ForeignKey("line_items.id"), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    create_table_if_missing(
        "driver_values",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("p10", sa.Float(), nullable=True),
        sa.Column("p90", sa.Float(), nullable=True),
        sa.Column("value_type", sa.String(32), nullable=False, server_default="actual"),
        sa.Column("version_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "driver_id",
            "period",
            "value_type",
            "version_id",
            name="uq_driver_values_driver_period_type_version",
        ),
    )
    create_index_if_missing(
        "ix_driver_values_driver_period", "driver_values", ["driver_id", "period"]
    )

    create_table_if_missing(
        "driver_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column(
            "line_item_id", sa.Integer(), sa.ForeignKey("line_items.id"), nullable=False
        ),
        sa.Column("link_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("relation", sa.String(32), nullable=False, server_default="level"),
        sa.Column("lag", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coefficient", sa.Float(), nullable=True),
        sa.Column("coefficient_se", sa.Float(), nullable=True),
        sa.Column("elasticity", sa.Float(), nullable=True),
        sa.Column("t_stat", sa.Float(), nullable=True),
        sa.Column("p_value", sa.Float(), nullable=True),
        sa.Column("p_value_adj", sa.Float(), nullable=True),
        sa.Column("r2", sa.Float(), nullable=True),
        sa.Column("n_obs", sa.Integer(), nullable=True),
        sa.Column("transform", sa.String(32), nullable=False, server_default="level"),
        sa.Column("fit_method", sa.String(64), nullable=True),
        sa.Column("hac_lags", sa.Integer(), nullable=True),
        sa.Column("diagnostics", sa.JSON(), nullable=True),
        sa.Column(
            "discovery_run_id",
            sa.String(36),
            sa.ForeignKey("driver_discovery_runs.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column("composition_group", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    create_index_if_missing("ix_driver_links_line_item", "driver_links", ["line_item_id"])
    create_index_if_missing("ix_driver_links_status", "driver_links", ["status"])
    create_index_if_missing(
        "ix_driver_links_composition", "driver_links", ["composition_group"]
    )


def downgrade() -> None:
    drop_table_if_exists("driver_links")
    drop_table_if_exists("driver_values")
    drop_table_if_exists("driver_discovery_runs")
    drop_table_if_exists("drivers")
    drop_column_if_exists("roles", "can_manage_drivers")
