"""Converge divergent dual-root schemas to the current ORM target.

Revision ID: 008_schema_repair
Revises: 007_fiscal_mint
Create Date: 2026-07-16

Inspects the live schema and adds/renames columns so installs that ran
either historical root (``001_initial`` or the old ``001`` fork) match
the SQLAlchemy models. Uses ``batch_alter_table`` for SQLite.
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

revision: str = "008_schema_repair"
down_revision: Union[str, None] = "007_fiscal_mint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _repair_conversations() -> None:
    add_column_if_missing(
        "conversations",
        sa.Column("working_memory", sa.JSON(), nullable=True),
    )


def _repair_forecast_line_results() -> None:
    for name, col in (
        ("review_status", sa.Column("review_status", sa.String(20), nullable=True)),
        ("review_comment", sa.Column("review_comment", sa.Text(), nullable=True)),
        ("reviewed_by", sa.Column("reviewed_by", sa.String(36), nullable=True)),
        ("reviewed_at", sa.Column("reviewed_at", sa.DateTime(), nullable=True)),
        ("ai_recommendation", sa.Column("ai_recommendation", sa.String(20), nullable=True)),
        ("ai_reasoning", sa.Column("ai_reasoning", sa.Text(), nullable=True)),
        ("ai_risk_score", sa.Column("ai_risk_score", sa.Float(), nullable=True)),
        ("bounds_method", sa.Column("bounds_method", sa.String(40), nullable=True)),
    ):
        add_column_if_missing("forecast_line_results", col)


def _repair_actuals_records() -> None:
    add_column_if_missing(
        "actuals_records",
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
    )


def _repair_actuals_datasets() -> None:
    if not table_exists("actuals_datasets"):
        return
    cols = _cols("actuals_datasets")

    # B-root used `name` / `uploaded_at` instead of source_name / ingested_at
    if "source_name" not in cols and "name" in cols:
        add_column_if_missing(
            "actuals_datasets",
            sa.Column("source_name", sa.String(255), server_default="", nullable=False),
        )
        op.execute(
            sa.text(
                "UPDATE actuals_datasets SET source_name = COALESCE(name, '') "
                "WHERE source_name IS NULL OR source_name = ''"
            )
        )
    else:
        add_column_if_missing(
            "actuals_datasets",
            sa.Column("source_name", sa.String(255), server_default="", nullable=False),
        )

    add_column_if_missing(
        "actuals_datasets",
        sa.Column("source_type", sa.String(50), server_default="csv", nullable=False),
    )
    add_column_if_missing(
        "actuals_datasets",
        sa.Column("periods_count", sa.Integer(), server_default="0", nullable=False),
    )
    add_column_if_missing(
        "actuals_datasets",
        sa.Column("missing_periods", sa.Text(), nullable=True),
    )
    add_column_if_missing(
        "actuals_datasets",
        sa.Column("completeness_pct", sa.Float(), server_default="100.0", nullable=False),
    )
    add_column_if_missing(
        "actuals_datasets",
        sa.Column("notes", sa.Text(), nullable=True),
    )

    if "ingested_at" not in cols:
        add_column_if_missing(
            "actuals_datasets",
            sa.Column("ingested_at", sa.DateTime(), nullable=True),
        )
        if "uploaded_at" in _cols("actuals_datasets"):
            op.execute(
                sa.text(
                    "UPDATE actuals_datasets SET ingested_at = uploaded_at "
                    "WHERE ingested_at IS NULL"
                )
            )

    # Drop B-only orphans after backfill (safe when unused by ORM)
    for orphan in ("upload_path", "uploaded_by", "categories_json", "name", "uploaded_at"):
        drop_column_if_exists("actuals_datasets", orphan)


def _repair_line_items() -> None:
    if not table_exists("line_items"):
        return
    cols = _cols("line_items")

    if "subcategory" not in cols and "sub_category" in cols:
        add_column_if_missing(
            "line_items",
            sa.Column("subcategory", sa.String(100), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE line_items SET subcategory = sub_category "
                "WHERE subcategory IS NULL"
            )
        )
        drop_column_if_exists("line_items", "sub_category")
    else:
        add_column_if_missing(
            "line_items",
            sa.Column("subcategory", sa.String(100), nullable=True),
        )

    for name, col in (
        ("business_unit", sa.Column("business_unit", sa.String(100), nullable=True)),
        ("geography", sa.Column("geography", sa.String(100), nullable=True)),
        ("product_line", sa.Column("product_line", sa.String(100), nullable=True)),
        (
            "is_calculated",
            sa.Column("is_calculated", sa.Boolean(), server_default="0", nullable=False),
        ),
        ("formula", sa.Column("formula", sa.Text(), nullable=True)),
        (
            "allow_negative",
            sa.Column("allow_negative", sa.Boolean(), server_default="0", nullable=False),
        ),
        (
            "display_order",
            sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        ),
    ):
        add_column_if_missing("line_items", col)

    # B-root stored formula as type+JSON; copy a string form into formula when empty
    if column_exists("line_items", "formula_definition"):
        op.execute(
            sa.text(
                "UPDATE line_items SET formula = CAST(formula_definition AS TEXT) "
                "WHERE formula IS NULL AND formula_definition IS NOT NULL"
            )
        )

    # sign_convention: Integer(B) → String(A/model)
    if column_exists("line_items", "sign_convention"):
        # Best-effort: if values look numeric, remap. SQLite stores loosely.
        op.execute(
            sa.text(
                "UPDATE line_items SET sign_convention = 'positive' "
                "WHERE sign_convention IN ('1', '1.0', 1)"
            )
        )
        op.execute(
            sa.text(
                "UPDATE line_items SET sign_convention = 'negative' "
                "WHERE sign_convention IN ('-1', '-1.0', -1)"
            )
        )

    for orphan in ("formula_type", "formula_definition", "dataset_id", "sub_category"):
        drop_column_if_exists("line_items", orphan)


def _repair_line_item_dependencies() -> None:
    if not table_exists("line_item_dependencies"):
        return
    cols = _cols("line_item_dependencies")

    # Ensure A/model columns exist
    add_column_if_missing(
        "line_item_dependencies",
        sa.Column("dependent_item_id", sa.Integer(), nullable=True),
    )
    add_column_if_missing(
        "line_item_dependencies",
        sa.Column("source_item_id", sa.Integer(), nullable=True),
    )
    add_column_if_missing(
        "line_item_dependencies",
        sa.Column(
            "relationship_type",
            sa.String(50),
            server_default="sum",
            nullable=False,
        ),
    )

    # Backfill from B-root names when present
    if "parent_id" in cols:
        op.execute(
            sa.text(
                "UPDATE line_item_dependencies "
                "SET dependent_item_id = COALESCE(dependent_item_id, parent_id)"
            )
        )
    if "child_id" in cols:
        op.execute(
            sa.text(
                "UPDATE line_item_dependencies "
                "SET source_item_id = COALESCE(source_item_id, child_id)"
            )
        )
    if "operation" in cols:
        op.execute(
            sa.text(
                "UPDATE line_item_dependencies SET relationship_type = "
                "CASE LOWER(COALESCE(operation, 'add')) "
                "WHEN 'add' THEN 'sum' "
                "WHEN 'subtract' THEN 'subtract' "
                "WHEN 'multiply' THEN 'multiply' "
                "ELSE COALESCE(relationship_type, 'sum') END "
                "WHERE operation IS NOT NULL"
            )
        )

    for orphan in ("parent_id", "child_id", "operation"):
        drop_column_if_exists("line_item_dependencies", orphan)


def _repair_overrides() -> None:
    if not table_exists("overrides"):
        return
    cols = _cols("overrides")

    add_column_if_missing(
        "overrides",
        sa.Column("original_model_value", sa.Float(), nullable=True),
    )
    if "original_value" in cols:
        op.execute(
            sa.text(
                "UPDATE overrides SET original_model_value = "
                "COALESCE(original_model_value, original_value) "
                "WHERE original_model_value IS NULL"
            )
        )

    add_column_if_missing(
        "overrides",
        sa.Column("user_id", sa.String(36), nullable=True),
    )
    if "created_by" in cols:
        op.execute(
            sa.text(
                "UPDATE overrides SET user_id = COALESCE(user_id, created_by) "
                "WHERE user_id IS NULL"
            )
        )

    add_column_if_missing(
        "overrides",
        sa.Column("status", sa.String(50), server_default="active", nullable=False),
    )
    add_column_if_missing(
        "overrides",
        sa.Column("carry_forward", sa.Boolean(), server_default="1", nullable=False),
    )
    add_column_if_missing(
        "overrides",
        sa.Column(
            "downstream_recalc_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    add_column_if_missing(
        "overrides",
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
    )
    add_column_if_missing(
        "overrides",
        sa.Column("reverted_by", sa.String(36), nullable=True),
    )

    for orphan in ("original_value", "created_by", "override_type", "expires_at"):
        drop_column_if_exists("overrides", orphan)


def _repair_roles_and_versions() -> None:
    add_column_if_missing(
        "roles",
        sa.Column("can_view_all_bus", sa.Boolean(), server_default="0", nullable=False),
    )
    add_column_if_missing(
        "forecast_versions",
        sa.Column("reporting_currency", sa.String(3), nullable=True),
    )
    add_column_if_missing(
        "forecast_versions",
        sa.Column("fx_rate_set_hash", sa.String(64), nullable=True),
    )


def _repair_driver_tables() -> None:
    """Converge B-shaped driver tables toward the A/model shape where possible."""
    if table_exists("driver_form_configs"):
        add_column_if_missing(
            "driver_form_configs",
            sa.Column("name", sa.String(255), server_default="", nullable=False),
        )
        add_column_if_missing(
            "driver_form_configs",
            sa.Column("description", sa.Text(), nullable=True),
        )
        add_column_if_missing(
            "driver_form_configs",
            sa.Column("fields_schema", sa.JSON(), nullable=True),
        )
        if column_exists("driver_form_configs", "fields") and column_exists(
            "driver_form_configs", "fields_schema"
        ):
            op.execute(
                sa.text(
                    "UPDATE driver_form_configs SET fields_schema = fields "
                    "WHERE fields_schema IS NULL AND fields IS NOT NULL"
                )
            )
        add_column_if_missing(
            "driver_form_configs",
            sa.Column("soft_deadline_days", sa.Integer(), server_default="3", nullable=False),
        )
        add_column_if_missing(
            "driver_form_configs",
            sa.Column("hard_deadline_days", sa.Integer(), server_default="5", nullable=False),
        )
        add_column_if_missing(
            "driver_form_configs",
            sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        )

    if table_exists("driver_inputs"):
        add_column_if_missing(
            "driver_inputs",
            sa.Column("form_config_id", sa.Integer(), nullable=True),
        )


def upgrade() -> None:
    _repair_conversations()
    _repair_forecast_line_results()
    _repair_actuals_records()
    _repair_actuals_datasets()
    _repair_line_items()
    _repair_line_item_dependencies()
    _repair_overrides()
    _repair_roles_and_versions()
    _repair_driver_tables()


def downgrade() -> None:
    # Non-destructive repair; downgrade is a no-op (cannot safely reintroduce forks).
    pass
