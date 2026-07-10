"""Add integration_connections + roles.can_view_all_bus.

Revision ID: 005_security_hardening
Revises: 004_merge_heads
Create Date: 2026-07-11
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
from migration_helpers import create_table_if_missing, drop_table_if_exists, table_exists

revision: str = "005_security_hardening"
down_revision: Union[str, None] = "004_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    create_table_if_missing(
        "integration_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("encrypted_url", sa.Text(), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )

    # can_view_all_bus on roles
    if table_exists("roles"):
        bind = op.get_bind()
        cols = {c["name"] for c in sa.inspect(bind).get_columns("roles")}
        if "can_view_all_bus" not in cols:
            op.add_column(
                "roles",
                sa.Column("can_view_all_bus", sa.Boolean(), server_default=sa.text("0")),
            )
            # Admins see all BUs by default
            op.execute(
                "UPDATE roles SET can_view_all_bus = 1 WHERE name = 'admin' OR can_admin = 1"
            )


def downgrade() -> None:
    if table_exists("roles"):
        bind = op.get_bind()
        cols = {c["name"] for c in sa.inspect(bind).get_columns("roles")}
        if "can_view_all_bus" in cols:
            op.drop_column("roles", "can_view_all_bus")
    drop_table_if_exists("integration_connections")
