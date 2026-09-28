"""Add model_p50 / benchmark columns and outlier metadata.

Revision ID: 010_accuracy_model_p50_outliers
Revises: 009_dedupe_uniques_indexes
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
from migration_helpers import add_column_if_missing, drop_column_if_exists  # noqa: E402

revision: str = "010_accuracy_model_p50_outliers"
down_revision: Union[str, None] = "009_dedupe_uniques_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    add_column_if_missing(
        "forecast_line_results",
        sa.Column("model_p50", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "forecast_accuracy_records",
        sa.Column("model_p50", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "forecast_accuracy_records",
        sa.Column("naive_p50", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "forecast_accuracy_records",
        sa.Column("seasonal_naive_p50", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "model_metadata",
        sa.Column("cleaned_periods", sa.JSON(), nullable=True),
    )
    add_column_if_missing(
        "model_metadata",
        sa.Column("outliers_cleaned", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    drop_column_if_exists("model_metadata", "outliers_cleaned")
    drop_column_if_exists("model_metadata", "cleaned_periods")
    drop_column_if_exists("forecast_accuracy_records", "seasonal_naive_p50")
    drop_column_if_exists("forecast_accuracy_records", "naive_p50")
    drop_column_if_exists("forecast_accuracy_records", "model_p50")
    drop_column_if_exists("forecast_line_results", "model_p50")
