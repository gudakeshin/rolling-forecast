"""Initial schema - all tables from Phase 1-4.

Revision ID: 001_initial
Revises: None
Create Date: 2026-02-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Roles
    op.create_table(
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
    op.create_table(
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
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(255), default="New Conversation"),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # Messages
    op.create_table(
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
    op.create_table(
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
    op.create_table(
        "line_item_dependencies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("dependent_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("source_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("relationship_type", sa.String(50), default="sum"),
        sa.Column("weight", sa.Float, default=1.0),
    )

    # Actuals Datasets
    op.create_table(
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
    op.create_table(
        "actuals_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("actuals_datasets.id"), nullable=False),
        sa.Column("line_item_id", sa.Integer, sa.ForeignKey("line_items.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), default="USD"),
    )

    # Forecast Versions
    op.create_table(
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
    op.create_table(
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
    op.create_table(
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
    op.create_table(
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
    op.create_table(
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
    op.create_table(
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
    op.drop_table("driver_inputs")
    op.drop_table("driver_form_configs")
    op.drop_table("overrides")
    op.drop_table("model_metadata")
    op.drop_table("forecast_line_results")
    op.drop_table("forecast_versions")
    op.drop_table("actuals_records")
    op.drop_table("actuals_datasets")
    op.drop_table("line_item_dependencies")
    op.drop_table("line_items")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
    op.drop_table("roles")
