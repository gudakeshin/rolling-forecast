"""Add model_mase/model_pinball, widen model_type, pin selection_rule.

Revision ID: 013_selection_metrics
Revises: 012_forecast_scenario
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
    add_column_if_missing,
    column_exists,
    drop_column_if_exists,
    table_exists,
)

revision: str = "013_selection_metrics"
down_revision: Union[str, None] = "012_forecast_scenario"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _widen_string(table: str, column: str, length: int = 120) -> None:
    if not table_exists(table) or not column_exists(table, column):
        return
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        # SQLite type affinity ignores length; batch alter keeps model in sync.
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=sa.String(50),
                type_=sa.String(length),
                existing_nullable=True if column != "model_type" or table != "model_metadata" else False,
            )
    else:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(50),
            type_=sa.String(length),
            existing_nullable=True if table == "forecast_line_results" else False,
        )


def upgrade() -> None:
    add_column_if_missing(
        "forecast_line_results",
        sa.Column("model_mase", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "forecast_line_results",
        sa.Column("model_pinball", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "forecast_versions",
        sa.Column("selection_rule", sa.String(64), nullable=True),
    )
    _widen_string("forecast_line_results", "model_type", 120)
    _widen_string("model_metadata", "model_type", 120)


def downgrade() -> None:
    drop_column_if_exists("forecast_line_results", "model_pinball")
    drop_column_if_exists("forecast_line_results", "model_mase")
    drop_column_if_exists("forecast_versions", "selection_rule")
    # Do not narrow model_type on downgrade — safer leave widened.
