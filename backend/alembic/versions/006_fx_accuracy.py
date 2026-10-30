"""Add FX rates, system settings, accuracy records; stamp ForecastVersion FX fields.

Revision ID: 006_fx_accuracy
Revises: 005_security_hardening
Create Date: 2026-07-11
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import create_table_if_missing, drop_table_if_exists, table_exists

revision: str = "006_fx_accuracy"
down_revision: Union[str, None] = "005_security_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "fx_rates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("from_currency", sa.String(3), nullable=False),
        sa.Column("to_currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("rate_type", sa.String(20), server_default="average"),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("from_currency", "to_currency", "period", "rate_type", name="uq_fx_rate"),
    )
    create_table_if_missing(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime()),
    )
    create_table_if_missing(
        "forecast_accuracy_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("forecast_versions.id"), nullable=False),
        sa.Column("line_item_id", sa.Integer(), sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("horizon_offset", sa.Integer(), nullable=False),
        sa.Column("predicted_p10", sa.Float(), nullable=True),
        sa.Column("predicted_p50", sa.Float(), nullable=False),
        sa.Column("predicted_p90", sa.Float(), nullable=True),
        sa.Column("actual", sa.Float(), nullable=False),
        sa.Column("absolute_error", sa.Float(), nullable=False),
        sa.Column("pct_error", sa.Float(), nullable=True),
        sa.Column("within_p10_p90", sa.Boolean(), nullable=True),
        sa.Column("model_type", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("version_id", "line_item_id", "period", name="uq_accuracy_version_line_period"),
    )

    if table_exists("forecast_versions"):
        bind = op.get_bind()
        cols = {c["name"] for c in sa.inspect(bind).get_columns("forecast_versions")}
        if "reporting_currency" not in cols:
            op.add_column("forecast_versions", sa.Column("reporting_currency", sa.String(3), nullable=True))
        if "fx_rate_set_hash" not in cols:
            op.add_column("forecast_versions", sa.Column("fx_rate_set_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    if table_exists("forecast_versions"):
        bind = op.get_bind()
        cols = {c["name"] for c in sa.inspect(bind).get_columns("forecast_versions")}
        if "fx_rate_set_hash" in cols:
            op.drop_column("forecast_versions", "fx_rate_set_hash")
        if "reporting_currency" in cols:
            op.drop_column("forecast_versions", "reporting_currency")
    drop_table_if_exists("forecast_accuracy_records")
    drop_table_if_exists("system_settings")
    drop_table_if_exists("fx_rates")
