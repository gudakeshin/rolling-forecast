"""Add persistent core memory blocks.

Revision ID: 015_memory_blocks
Revises: 014_driver_model
Create Date: 2026-07-28
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
    create_index_if_missing,
    create_table_if_missing,
    drop_table_if_exists,
)

revision: str = "015_memory_blocks"
down_revision: Union[str, None] = "014_driver_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "memory_blocks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=True),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("char_limit", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "scope",
            "owner_id",
            "label",
            name="uq_memory_blocks_scope_owner_label",
        ),
    )
    create_index_if_missing("ix_memory_blocks_scope", "memory_blocks", ["scope"])
    create_index_if_missing(
        "ix_memory_blocks_scope_owner", "memory_blocks", ["scope", "owner_id"]
    )


def downgrade() -> None:
    drop_table_if_exists("memory_blocks")
