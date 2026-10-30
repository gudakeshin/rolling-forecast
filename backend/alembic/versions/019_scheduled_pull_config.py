"""Add opt-in scheduled-pull config to integration_connections.

Revision ID: 019_scheduled_pull_config
Revises: 018_sign_priors
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
from migration_helpers import add_column_if_missing, drop_column_if_exists  # noqa: E402

revision: str = "019_scheduled_pull_config"
down_revision: Union[str, None] = "018_sign_priors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "integration_connections",
        sa.Column("auto_pull_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    add_column_if_missing(
        "integration_connections",
        sa.Column("default_query", sa.Text(), nullable=True),
    )
    add_column_if_missing(
        "integration_connections",
        sa.Column("default_relative_path", sa.Text(), nullable=True),
    )
    add_column_if_missing(
        "integration_connections",
        sa.Column("default_source_name", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    drop_column_if_exists("integration_connections", "default_source_name")
    drop_column_if_exists("integration_connections", "default_relative_path")
    drop_column_if_exists("integration_connections", "default_query")
    drop_column_if_exists("integration_connections", "auto_pull_enabled")
