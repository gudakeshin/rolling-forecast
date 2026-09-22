"""Add bounds_method; widen period labels for 4-4-5; fiscal calendar setting.

Revision ID: 007_fiscal_mint
Revises: 006_fx_accuracy
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
from migration_helpers import table_exists

revision: str = "007_fiscal_mint"
down_revision: Union[str, None] = "006_fx_accuracy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if table_exists("forecast_line_results"):
        cols = {c["name"] for c in sa.inspect(bind).get_columns("forecast_line_results")}
        if "bounds_method" not in cols:
            op.add_column(
                "forecast_line_results",
                sa.Column("bounds_method", sa.String(40), nullable=True),
            )
        # Widen period for FY2026-P01 style labels (was String(7) for YYYY-MM)
        try:
            op.alter_column(
                "forecast_line_results",
                "period",
                existing_type=sa.String(7),
                type_=sa.String(16),
                existing_nullable=False,
            )
        except Exception:
            pass

    for table, col in (
        ("forecast_versions", "base_period"),
        ("fx_rates", "period"),
        ("forecast_accuracy_records", "period"),
        ("actuals_records", "period"),
        ("budget_line_items", "period"),
        ("overrides", "period"),
    ):
        if not table_exists(table):
            continue
        try:
            op.alter_column(
                table,
                col,
                existing_type=sa.String(7),
                type_=sa.String(16),
                existing_nullable=True,
            )
        except Exception:
            pass


def downgrade() -> None:
    if table_exists("forecast_line_results"):
        bind = op.get_bind()
        cols = {c["name"] for c in sa.inspect(bind).get_columns("forecast_line_results")}
        if "bounds_method" in cols:
            op.drop_column("forecast_line_results", "bounds_method")
