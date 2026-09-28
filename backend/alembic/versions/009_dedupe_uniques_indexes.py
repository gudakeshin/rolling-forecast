"""Dedupe volume tables, add unique constraints and indexes.

Revision ID: 009_dedupe_uniques_indexes
Revises: 008_schema_repair
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
    table_exists,
    unique_constraint_exists,
)

revision: str = "009_dedupe_uniques_indexes"
down_revision: Union[str, None] = "008_schema_repair"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UQ_FLR = "uq_flr_version_line_period"
UQ_ACT = "uq_actuals_dataset_line_period"


def _dedupe_forecast_line_results() -> None:
    """Keep the newest row per (version_id, line_item_id, period)."""
    if not table_exists("forecast_line_results"):
        return
    # Prefer higher rowid / lexicographically greater id as "newest"
    op.execute(
        sa.text(
            """
            DELETE FROM forecast_line_results
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY version_id, line_item_id, period
                               ORDER BY id DESC
                           ) AS rn
                    FROM forecast_line_results
                ) ranked
                WHERE rn > 1
            )
            """
        )
    )


def _dedupe_actuals_records() -> None:
    """Keep the newest (highest id) row per (dataset_id, line_item_id, period)."""
    if not table_exists("actuals_records"):
        return
    op.execute(
        sa.text(
            """
            DELETE FROM actuals_records
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY dataset_id, line_item_id, period
                               ORDER BY id DESC
                           ) AS rn
                    FROM actuals_records
                ) ranked
                WHERE rn > 1
            )
            """
        )
    )


def _add_uniques() -> None:
    if table_exists("forecast_line_results") and not unique_constraint_exists(
        "forecast_line_results", UQ_FLR
    ):
        with op.batch_alter_table("forecast_line_results") as batch_op:
            batch_op.create_unique_constraint(
                UQ_FLR, ["version_id", "line_item_id", "period"]
            )

    if table_exists("actuals_records") and not unique_constraint_exists(
        "actuals_records", UQ_ACT
    ):
        with op.batch_alter_table("actuals_records") as batch_op:
            batch_op.create_unique_constraint(
                UQ_ACT, ["dataset_id", "line_item_id", "period"]
            )


def _add_indexes() -> None:
    create_index_if_missing(
        "ix_flr_version_id", "forecast_line_results", ["version_id"]
    )
    create_index_if_missing(
        "ix_flr_line_item_period",
        "forecast_line_results",
        ["line_item_id", "period"],
    )
    create_index_if_missing(
        "ix_actuals_line_item_period",
        "actuals_records",
        ["line_item_id", "period"],
    )
    create_index_if_missing("ix_overrides_version_id", "overrides", ["version_id"])


def upgrade() -> None:
    _dedupe_forecast_line_results()
    _dedupe_actuals_records()
    _add_uniques()
    _add_indexes()


def downgrade() -> None:
    if table_exists("overrides"):
        op.drop_index("ix_overrides_version_id", table_name="overrides")
    if table_exists("actuals_records"):
        op.drop_index("ix_actuals_line_item_period", table_name="actuals_records")
        with op.batch_alter_table("actuals_records") as batch_op:
            batch_op.drop_constraint(UQ_ACT, type_="unique")
    if table_exists("forecast_line_results"):
        op.drop_index("ix_flr_line_item_period", table_name="forecast_line_results")
        op.drop_index("ix_flr_version_id", table_name="forecast_line_results")
        with op.batch_alter_table("forecast_line_results") as batch_op:
            batch_op.drop_constraint(UQ_FLR, type_="unique")
