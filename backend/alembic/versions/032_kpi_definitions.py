"""Custom KPI definitions: per-company formulas over P&L lines and categories.

Revision ID: 032_kpi_definitions
Revises: 031_saved_views
Create Date: 2026-10-29
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa

_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import create_table_if_missing, drop_table_if_exists  # noqa: E402

revision: str = "032_kpi_definitions"
down_revision: Union[str, None] = "031_saved_views"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "kpi_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "business_unit_id",
            sa.String(36),
            sa.ForeignKey("business_units.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("display_format", sa.String(16), nullable=False, server_default="number"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("business_unit_id", "name", name="uq_kpi_definitions_bu_name"),
    )


def downgrade() -> None:
    drop_table_if_exists("kpi_definitions")
