"""Add anomaly_dismissals table for server-side anomaly review state.

Revision ID: 020_anomaly_dismissals
Revises: 019_scheduled_pull_config
Create Date: 2026-09-14
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
    create_index_if_missing,
    create_table_if_missing,
    drop_table_if_exists,
)

revision: str = "020_anomaly_dismissals"
down_revision: Union[str, None] = "019_scheduled_pull_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "anomaly_dismissals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "version_id",
            sa.String(length=36),
            sa.ForeignKey("forecast_versions.id"),
            nullable=False,
        ),
        sa.Column("anomaly_id", sa.String(length=36), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "anomaly_id", name="uq_anomaly_dismissals_user_anomaly"),
    )
    create_index_if_missing(
        "ix_anomaly_dismissals_version_id", "anomaly_dismissals", ["version_id"]
    )
    create_index_if_missing(
        "ix_anomaly_dismissals_user_id", "anomaly_dismissals", ["user_id"]
    )


def downgrade() -> None:
    drop_table_if_exists("anomaly_dismissals")
