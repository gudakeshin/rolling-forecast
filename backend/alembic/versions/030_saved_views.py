"""Add saved_views table -- server-backed replacement for the frontend's
localStorage-only savedViewsStore.

Revision ID: 030_saved_views
Revises: 029_heuristic_and_memory_scoping
Create Date: 2026-09-29
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

revision: str = "030_saved_views"
down_revision: Union[str, None] = "029_heuristic_and_memory_scoping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "saved_views",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("panel_type", sa.String(length=50), nullable=False),
        sa.Column("panel_params", sa.JSON(), nullable=False),
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_saved_views_business_unit_id"),
            nullable=False,
        ),
        sa.Column(
            "created_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "name", "business_unit_id", "created_by", name="uq_saved_views_name_bu_user"
        ),
    )
    create_index_if_missing(
        "ix_saved_views_business_unit_id", "saved_views", ["business_unit_id"]
    )
    create_index_if_missing("ix_saved_views_created_by", "saved_views", ["created_by"])


def downgrade() -> None:
    drop_table_if_exists("saved_views")
