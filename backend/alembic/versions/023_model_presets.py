"""Add model_presets table and forecast_versions.model_preset_id.

Revision ID: 023_model_presets
Revises: 022_review_undo_snapshots
Create Date: 2026-09-15
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path
from typing import Sequence, Union

import sqlalchemy as sa

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

revision: str = "023_model_presets"
down_revision: Union[str, None] = "022_review_undo_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "model_presets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_type", sa.String(length=50), nullable=False),
        sa.Column("candidate_models", sa.JSON(), nullable=True),
        sa.Column("default_horizon_months", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_model_presets_name"),
    )
    create_index_if_missing("ix_model_presets_name", "model_presets", ["name"])

    add_column_if_missing(
        "forecast_versions",
        sa.Column(
            "model_preset_id",
            sa.String(length=36),
            sa.ForeignKey(
                "model_presets.id", name="fk_forecast_versions_model_preset_id"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    drop_column_if_exists("forecast_versions", "model_preset_id")
    drop_table_if_exists("model_presets")
