"""Server-side saved views (were per-browser localStorage only).

Revision ID: 031_saved_views
Revises: 030_platform_admin_oidc
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
from migration_helpers import (  # noqa: E402
    create_index_if_missing,
    create_table_if_missing,
    drop_table_if_exists,
)

revision: str = "031_saved_views"
down_revision: Union[str, None] = "030_platform_admin_oidc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "saved_views",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "business_unit_id",
            sa.String(36),
            sa.ForeignKey("business_units.id"),
            nullable=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("panel_type", sa.String(64), nullable=False),
        sa.Column("panel_params", sa.JSON(), nullable=False),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    create_index_if_missing(
        "ix_saved_views_bu_user", "saved_views", ["business_unit_id", "user_id"]
    )


def downgrade() -> None:
    drop_table_if_exists("saved_views")
