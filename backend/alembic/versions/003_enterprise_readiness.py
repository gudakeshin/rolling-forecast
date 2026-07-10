"""Enterprise readiness tables: audit, budget, approval workflows."""

from alembic import op
import sqlalchemy as sa

revision = "003_enterprise_readiness"
down_revision = "002_add_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
        sa.Column("actor_id", sa.String(36), nullable=True, index=True),
        sa.Column("actor_username", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("entity_type", sa.String(100), nullable=False, index=True),
        sa.Column("entity_id", sa.String(100), nullable=True, index=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("retention_days", sa.Integer(), server_default="2555"),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "budget_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), server_default="active"),
        sa.Column("source_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "budget_line_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("budget_version_id", sa.String(36), sa.ForeignKey("budget_versions.id"), nullable=False, index=True),
        sa.Column("line_item_id", sa.Integer(), sa.ForeignKey("line_items.id"), nullable=False, index=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
    )

    op.create_table(
        "approval_workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1")),
        sa.Column("levels", sa.JSON(), nullable=False),
        sa.Column("require_sod", sa.Boolean(), server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime()),
    )

    op.create_table(
        "approval_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("forecast_versions.id"), nullable=False, index=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("approval_workflows.id"), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("required_role", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("approval_steps")
    op.drop_table("approval_workflows")
    op.drop_table("budget_line_items")
    op.drop_table("budget_versions")
    op.drop_table("audit_events")
