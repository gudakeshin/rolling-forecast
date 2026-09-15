"""Persist pre-MinT p50 for honest reconciliation attribution.

Revision ID: 016_pre_reconcile_p50
Revises: 015_memory_blocks
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
from migration_helpers import add_column_if_missing, drop_column_if_exists  # noqa: E402

revision: str = "016_pre_reconcile_p50"
down_revision: Union[str, None] = "015_memory_blocks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "forecast_line_results",
        sa.Column("pre_reconcile_p50", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    drop_column_if_exists("forecast_line_results", "pre_reconcile_p50")
