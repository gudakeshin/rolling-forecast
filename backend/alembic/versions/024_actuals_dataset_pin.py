"""Add actuals_datasets.is_pinned and .integration_connection_id.

Revision ID: 024_actuals_dataset_pin
Revises: 023_model_presets
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
from migration_helpers import add_column_if_missing, drop_column_if_exists  # noqa: E402

revision: str = "024_actuals_dataset_pin"
down_revision: Union[str, None] = "023_model_presets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "actuals_datasets",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    add_column_if_missing(
        "actuals_datasets",
        sa.Column(
            "integration_connection_id",
            sa.String(length=36),
            sa.ForeignKey(
                "integration_connections.id", name="fk_actuals_datasets_integration_connection_id"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    drop_column_if_exists("actuals_datasets", "integration_connection_id")
    drop_column_if_exists("actuals_datasets", "is_pinned")
