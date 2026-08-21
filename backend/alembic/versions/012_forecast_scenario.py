"""Add scenario column to forecast_versions.

Revision ID: 012_forecast_scenario
Revises: 011_token_denylist_refresh
Create Date: 2026-07-16
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
from migration_helpers import (  # noqa: E402
    add_column_if_missing,
    create_index_if_missing,
    drop_column_if_exists,
)

revision: str = "012_forecast_scenario"
down_revision: Union[str, None] = "011_token_denylist_refresh"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "forecast_versions",
        sa.Column("scenario", sa.String(64), server_default="base", nullable=False),
    )
    create_index_if_missing("ix_forecast_versions_scenario", "forecast_versions", ["scenario"])


def downgrade() -> None:
    drop_column_if_exists("forecast_versions", "scenario")
