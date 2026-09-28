"""Refresh tokens + JWT denylist tables.

Revision ID: 011_token_denylist_refresh
Revises: 010_accuracy_model_p50_outliers
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
    create_index_if_missing,
    create_table_if_missing,
    drop_table_if_exists,
)

revision: str = "011_token_denylist_refresh"
down_revision: Union[str, None] = "010_accuracy_model_p50_outliers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("replaced_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    create_index_if_missing("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    create_index_if_missing("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    create_table_if_missing(
        "token_denylist",
        sa.Column("jti", sa.String(36), primary_key=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    create_index_if_missing("ix_token_denylist_expires_at", "token_denylist", ["expires_at"])


def downgrade() -> None:
    drop_table_if_exists("token_denylist")
    drop_table_if_exists("refresh_tokens")
