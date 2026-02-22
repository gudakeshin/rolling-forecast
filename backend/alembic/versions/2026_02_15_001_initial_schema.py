"""Initial schema — all tables from Phase 1-4.

Revision ID: 001
Revises: None
Create Date: 2026-02-15

This migration creates the complete database schema as it existed
after Phases 1-4 of development. For existing installations that
used create_all(), run `alembic stamp head` to mark this as applied.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Roles ─────────────────────────────────────────
    op.create_table(
        'roles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(50), nullable=False),
        sa.Column('description', sa.String(255), server_default=''),
        sa.Column('can_input', sa.Boolean(), server_default='0'),
        sa.Column('can_generate', sa.Boolean(), server_default='0'),
        sa.Column('can_override', sa.Boolean(), server_default='0'),
        sa.Column('can_review', sa.Boolean(), server_default='0'),
        sa.Column('can_publish', sa.Boolean(), server_default='0'),
        sa.Column('can_admin', sa.Boolean(), server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    # ── Users ─────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('username', sa.String(100), nullable=False),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('full_name', sa.String(255), server_default=''),
        sa.Column('business_unit', sa.String(100), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='1'),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id'), nullable=False),
        sa.Column('created_at', sa.DateTime()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username'),
    )

    # ── Conversations ─────────────────────────────────
    op.create_table(
        'conversations',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(255), server_default='New Conversation'),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime()),
        sa.Column('working_memory', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Messages ──────────────────────────────────────
    op.create_table(
        'messages',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_blocks', sa.JSON(), nullable=True),
        sa.Column('tool_calls', sa.JSON(), nullable=True),
        sa.Column('panel_payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Actuals Datasets ──────────────────────────────
    op.create_table(
        'actuals_datasets',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=True),
        sa.Column('upload_path', sa.String(500), nullable=True),
        sa.Column('uploaded_by', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('uploaded_at', sa.DateTime()),
        sa.Column('row_count', sa.Integer(), server_default='0'),
        sa.Column('period_start', sa.String(7), nullable=True),
        sa.Column('period_end', sa.String(7), nullable=True),
        sa.Column('categories_json', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Line Items ────────────────────────────────────
    op.create_table(
        'line_items',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('account_code', sa.String(50), nullable=True),
        sa.Column('category', sa.String(100), nullable=True),
        sa.Column('sub_category', sa.String(100), nullable=True),
        sa.Column('indent_level', sa.Integer(), server_default='0'),
        sa.Column('is_subtotal', sa.Boolean(), server_default='0'),
        sa.Column('formula_type', sa.String(50), nullable=True),
        sa.Column('formula_definition', sa.JSON(), nullable=True),
        sa.Column('sign_convention', sa.Integer(), server_default='1'),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('actuals_datasets.id'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Line Item Dependencies ────────────────────────
    op.create_table(
        'line_item_dependencies',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('line_items.id'), nullable=False),
        sa.Column('child_id', sa.Integer(), sa.ForeignKey('line_items.id'), nullable=False),
        sa.Column('operation', sa.String(20), server_default='add'),
        sa.Column('weight', sa.Float(), server_default='1.0'),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Actuals Records ───────────────────────────────
    op.create_table(
        'actuals_records',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('actuals_datasets.id'), nullable=False),
        sa.Column('line_item_id', sa.Integer(), sa.ForeignKey('line_items.id'), nullable=False),
        sa.Column('period', sa.String(7), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Forecast Versions ─────────────────────────────
    op.create_table(
        'forecast_versions',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('label', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), server_default='draft'),
        sa.Column('version_type', sa.String(50), server_default='scheduled'),
        sa.Column('parent_version_id', sa.String(36), sa.ForeignKey('forecast_versions.id'), nullable=True),
        sa.Column('actuals_dataset_id', sa.String(36), sa.ForeignKey('actuals_datasets.id'), nullable=True),
        sa.Column('actuals_hash', sa.String(64), nullable=True),
        sa.Column('input_hash', sa.String(64), nullable=True),
        sa.Column('horizon_months', sa.Integer(), server_default='12'),
        sa.Column('base_period', sa.String(7), nullable=True),
        sa.Column('model_versions', sa.JSON(), nullable=True),
        sa.Column('random_seed', sa.Integer(), server_default='42'),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approved_by', sa.String(36), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('total_line_items', sa.Integer(), server_default='0'),
        sa.Column('high_confidence_count', sa.Integer(), server_default='0'),
        sa.Column('medium_confidence_count', sa.Integer(), server_default='0'),
        sa.Column('low_confidence_count', sa.Integer(), server_default='0'),
        sa.Column('override_count', sa.Integer(), server_default='0'),
        sa.Column('generation_time_seconds', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Forecast Line Results ─────────────────────────
    op.create_table(
        'forecast_line_results',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('version_id', sa.String(36), sa.ForeignKey('forecast_versions.id'), nullable=False),
        sa.Column('line_item_id', sa.Integer(), sa.ForeignKey('line_items.id'), nullable=False),
        sa.Column('period', sa.String(7), nullable=False),
        sa.Column('p10', sa.Float(), nullable=True),
        sa.Column('p50', sa.Float(), nullable=False),
        sa.Column('p90', sa.Float(), nullable=True),
        sa.Column('confidence_score', sa.Float(), server_default='0.0'),
        sa.Column('confidence_level', sa.String(20), server_default='low'),
        sa.Column('model_type', sa.String(50), nullable=True),
        sa.Column('model_mape', sa.Float(), nullable=True),
        sa.Column('model_r_squared', sa.Float(), nullable=True),
        sa.Column('is_overridden', sa.Boolean(), server_default='0'),
        sa.Column('override_value', sa.Float(), nullable=True),
        sa.Column('is_calculated', sa.Boolean(), server_default='0'),
        sa.Column('review_status', sa.String(20), nullable=True),
        sa.Column('review_comment', sa.Text(), nullable=True),
        sa.Column('reviewed_by', sa.String(36), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('ai_recommendation', sa.String(20), nullable=True),
        sa.Column('ai_reasoning', sa.Text(), nullable=True),
        sa.Column('ai_risk_score', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Model Metadata ────────────────────────────────
    op.create_table(
        'model_metadata',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('line_result_id', sa.String(36), sa.ForeignKey('forecast_line_results.id'), nullable=False),
        sa.Column('model_type', sa.String(50), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=True),
        sa.Column('training_window_start', sa.String(7), nullable=True),
        sa.Column('training_window_end', sa.String(7), nullable=True),
        sa.Column('training_points', sa.Integer(), server_default='0'),
        sa.Column('mape', sa.Float(), nullable=True),
        sa.Column('r_squared', sa.Float(), nullable=True),
        sa.Column('aic', sa.Float(), nullable=True),
        sa.Column('bic', sa.Float(), nullable=True),
        sa.Column('seasonality_detected', sa.Boolean(), server_default='0'),
        sa.Column('seasonality_period', sa.Integer(), nullable=True),
        sa.Column('structural_break_detected', sa.Boolean(), server_default='0'),
        sa.Column('structural_break_period', sa.String(7), nullable=True),
        sa.Column('random_seed', sa.Integer(), server_default='42'),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Overrides ─────────────────────────────────────
    op.create_table(
        'overrides',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('version_id', sa.String(36), sa.ForeignKey('forecast_versions.id'), nullable=False),
        sa.Column('line_item_id', sa.Integer(), sa.ForeignKey('line_items.id'), nullable=False),
        sa.Column('period', sa.String(7), nullable=False),
        sa.Column('original_value', sa.Float(), nullable=True),
        sa.Column('override_value', sa.Float(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('override_type', sa.String(50), server_default='manual'),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Driver Inputs ─────────────────────────────────
    op.create_table(
        'driver_inputs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('version_id', sa.String(36), sa.ForeignKey('forecast_versions.id'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('business_unit', sa.String(100), nullable=True),
        sa.Column('status', sa.String(50), server_default='pending'),
        sa.Column('values', sa.JSON(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('review_comments', sa.Text(), nullable=True),
        sa.Column('is_late', sa.Boolean(), server_default='0'),
        sa.Column('due_date', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── Driver Form Configs ───────────────────────────
    op.create_table(
        'driver_form_configs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('version_id', sa.String(36), sa.ForeignKey('forecast_versions.id'), nullable=False),
        sa.Column('business_unit', sa.String(100), nullable=True),
        sa.Column('fields', sa.JSON(), nullable=True),
        sa.Column('instructions', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('driver_form_configs')
    op.drop_table('driver_inputs')
    op.drop_table('overrides')
    op.drop_table('model_metadata')
    op.drop_table('forecast_line_results')
    op.drop_table('forecast_versions')
    op.drop_table('actuals_records')
    op.drop_table('line_item_dependencies')
    op.drop_table('line_items')
    op.drop_table('actuals_datasets')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('users')
    op.drop_table('roles')
