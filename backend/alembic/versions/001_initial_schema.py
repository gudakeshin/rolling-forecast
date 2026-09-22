"""Initial schema - all tables from Phase 1-4.

Revision ID: 001_initial
Revises: None
Create Date: 2026-02-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

import sys
from pathlib import Path as _Path
_alembic_dir = str(_Path(__file__).resolve().parents[1])
if _alembic_dir not in sys.path:
    sys.path.insert(0, _alembic_dir)
from migration_helpers import create_table_if_missing, drop_table_if_exists

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Roles
    create_table_if_missing(
        "roles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(50), unique=True, nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("can_input", sa.Boolean, default=False),
        sa.Column("can_generate", sa.Boolean, default=False),
        sa.Column("can_override", sa.Boolean, default=False),
        sa.Column("can_review", sa.Boolean, default=False),
        sa.Column("can_publish", sa.Boolean, default=False),
        sa.Column("can_admin", sa.Boolean, default=False),
    )

    # Users
    create_table_if_missing(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("business_unit", sa.String(100), nullable=True),
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id"), nullable=True),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime),
    )

    # Conversations
    create_table_if_missing(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(255), default="New Conversation"),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # Messages
    create_table_if_missing(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_blocks", sa.JSON, nullable=True),
        sa.Column("tool_calls", sa.JSON, nullable=True),
        sa.Column("panel_payload", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )

    # Line Items
    create_table_if_missing(
        "line_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("account_code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("business_unit", sa.String(100), nullable=True),
        sa.Column("geography", sa.String(100), nullable=True),
        sa.Column("product_line", sa.String(100), nullable=True),
        sa.Column("is_calculated", sa.Boolean, default=False),
        sa.Column("formula", sa.Text, nullable=True),
        sa.Column("sign_convention", sa.String(20), default="positive"),
        sa.Column("allow_negative", sa.Boolean, default=False),
        sa.Column("display_order", sa.Integer, default=0),
        sa.Column("indent_level", sa.Integer, default=0),
        sa.Column("is_subtotal", sa.Boolean, default=False),
    )

    # Line Item Dependencies
    create_table_if_missing(
        "line_item_dependencies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("dependent_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("source_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("relationship_type", sa.String(50), default="sum"),
        sa.Column("weight", sa.Float, default=1.0),
    )

    # Actuals Datasets
    create_table_if_missing(
        "actuals_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_name", sa.String(255), default=""),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer, default=0),
        sa.Column("period_start", sa.String(7), nullable=False),
        sa.Column("period_end", sa.String(7), nullable=False),
        sa.Column("periods_count", sa.Integer, default=0),
        sa.Column("missing_periods", sa.Text, nullable=True),
        sa.Column("completeness_pct", sa.Float, default=100.0),
        sa.Column("ingested_at", sa.DateTime),
        sa.Column("notes", sa.Text, nullable=True),
    )

    # Actuals Records
    create_table_if_missing(
        "actuals_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("actuals_datasets.id"), nullable=False),
        sa.Column("line_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), default="USD"),
    )

    # Forecast Versions
    create_table_if_missing(
        "forecast_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), default="draft"),
        sa.Column("version_type", sa.String(50), default="scheduled"),
        sa.Column("parent_version_id", sa.String(36), sa.ForeignKey("forecast_versions.id"), nullable=True),
        sa.Column("actuals_dataset_id", sa.String(36), sa.ForeignKey("actuals_datasets.id"), nullable=True),
        sa.Column("actuals_hash", sa.String(64), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("horizon_months", sa.Integer, default=12),
        sa.Column("base_period", sa.String(7), nullable=True),
        sa.Column("model_versions", sa.JSON, nullable=True),
        sa.Column("random_seed", sa.Integer, default=42),
        sa.Column("created_at", sa.DateTime),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime, nullable=True),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("published_at", sa.DateTime, nullable=True),
        sa.Column("total_line_items", sa.Integer, default=0),
        sa.Column("high_confidence_count", sa.Integer, default=0),
        sa.Column("medium_confidence_count", sa.Integer, default=0),
        sa.Column("low_confidence_count", sa.Integer, default=0),
        sa.Column("override_count", sa.Integer, default=0),
        sa.Column("generation_time_seconds", sa.Float, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )

    # Forecast Line Results
    create_table_if_missing(
        "forecast_line_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("forecast_versions.id"), nullable=False),
        sa.Column("line_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("p10", sa.Float, nullable=True),
        sa.Column("p50", sa.Float, nullable=False),
        sa.Column("p90", sa.Float, nullable=True),
        sa.Column("confidence_score", sa.Float, default=0.0),
        sa.Column("confidence_level", sa.String(20), default="low"),
        sa.Column("model_type", sa.String(50), nullable=True),
        sa.Column("model_mape", sa.Float, nullable=True),
        sa.Column("model_r_squared", sa.Float, nullable=True),
        sa.Column("is_overridden", sa.Boolean, default=False),
        sa.Column("override_value", sa.Float, nullable=True),
        sa.Column("is_calculated", sa.Boolean, default=False),
    )

    # Model Metadata
    create_table_if_missing(
        "model_metadata",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("line_result_id", sa.String(36), sa.ForeignKey("forecast_line_results.id"), nullable=False),
        sa.Column("model_type", sa.String(50), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=True),
        sa.Column("training_window_start", sa.String(7), nullable=True),
        sa.Column("training_window_end", sa.String(7), nullable=True),
        sa.Column("training_points", sa.Integer, default=0),
        sa.Column("mape", sa.Float, nullable=True),
        sa.Column("r_squared", sa.Float, nullable=True),
        sa.Column("aic", sa.Float, nullable=True),
        sa.Column("bic", sa.Float, nullable=True),
        sa.Column("seasonality_detected", sa.Boolean, default=False),
        sa.Column("seasonality_period", sa.Integer, nullable=True),
        sa.Column("structural_break_detected", sa.Boolean, default=False),
        sa.Column("structural_break_period", sa.String(7), nullable=True),
        sa.Column("random_seed", sa.Integer, default=42),
    )

    # Overrides
    create_table_if_missing(
        "overrides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("forecast_versions.id"), nullable=False),
        sa.Column("line_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("original_model_value", sa.Float, nullable=False),
        sa.Column("override_value", sa.Float, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(50), default="active"),
        sa.Column("carry_forward", sa.Boolean, default=False),
        sa.Column("downstream_recalc_count", sa.Integer, default=0),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("expires_at", sa.DateTime, nullable=True),
    )

    # Driver Form Configs
    create_table_if_missing(
        "driver_form_configs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("business_unit", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("fields_schema", sa.JSON, nullable=False),
        sa.Column("soft_deadline_days", sa.Integer, default=3),
        sa.Column("hard_deadline_days", sa.Integer, default=5),
        sa.Column("is_active", sa.Boolean, default=True),
    )

    # Driver Inputs
    create_table_if_missing(
        "driver_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("forecast_versions.id"), nullable=False),
        sa.Column("form_config_id", sa.Integer, sa.ForeignKey("driver_form_configs.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("business_unit", sa.String(100), nullable=False),
        sa.Column("values", sa.JSON, nullable=False),
        sa.Column("status", sa.String(50), default="submitted"),
        sa.Column("submitted_at", sa.DateTime),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime, nullable=True),
        sa.Column("review_comments", sa.Text, nullable=True),
        sa.Column("is_late", sa.Boolean, default=False),
    )


def downgrade() -> None:
    drop_table_if_exists("driver_inputs")
    drop_table_if_exists("driver_form_configs")
    drop_table_if_exists("overrides")
    drop_table_if_exists("model_metadata")
    drop_table_if_exists("forecast_line_results")
    drop_table_if_exists("forecast_versions")
    drop_table_if_exists("actuals_records")
    drop_table_if_exists("actuals_datasets")
    drop_table_if_exists("line_item_dependencies")
    drop_table_if_exists("line_items")
    drop_table_if_exists("messages")
    drop_table_if_exists("conversations")
    drop_table_if_exists("users")
    drop_table_if_exists("roles")
