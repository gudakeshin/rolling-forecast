"""Add review_undo_snapshots table for the review undo toast (Phase 4.4).

Revision ID: 022_review_undo_snapshots
Revises: 021_notifications
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
    create_table_if_missing,
    drop_table_if_exists,
)

revision: str = "022_review_undo_snapshots"
down_revision: Union[str, None] = "021_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "review_undo_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "version_id",
            sa.String(length=36),
            sa.ForeignKey("forecast_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "actor_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("prior_state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    drop_table_if_exists("review_undo_snapshots")
