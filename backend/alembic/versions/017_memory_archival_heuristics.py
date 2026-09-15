"""Add learned heuristics (M3 reflection) and line-item target-bearing flag.

Revision ID: 017_memory_archival_heuristics
Revises: 016_pre_reconcile_p50
Create Date: 2026-07-28
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

revision: str = "017_memory_archival_heuristics"
down_revision: Union[str, None] = "016_pre_reconcile_p50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "learned_heuristics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="line_item"),
        sa.Column(
            "line_item_id",
            sa.Integer(),
            sa.ForeignKey("line_items.id"),
            nullable=True,
        ),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("model_type", sa.String(length=50), nullable=True),
        sa.Column("horizon_bucket", sa.String(length=20), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("effect_size", sa.Float(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="candidate"),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("proposed_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("review_by", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    create_index_if_missing(
        "ix_learned_heuristics_kind_status", "learned_heuristics", ["kind", "status"]
    )
    create_index_if_missing(
        "ix_learned_heuristics_line_item_id", "learned_heuristics", ["line_item_id"]
    )

    add_column_if_missing(
        "line_items",
        sa.Column("is_target_bearing", sa.Boolean(), nullable=True, server_default=sa.true()),
    )


def downgrade() -> None:
    drop_column_if_exists("line_items", "is_target_bearing")
    drop_table_if_exists("learned_heuristics")
