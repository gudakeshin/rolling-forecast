"""Add notes column to forecast_versions for scenario branching.

Revision ID: 002_add_notes
Revises: 001_initial
Create Date: 2026-02-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002_add_notes"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The notes column was included in the initial schema (001).
    # This migration exists as a template for future schema changes
    # and to document that the `notes` column was added in Phase 4
    # for scenario branching support.
    #
    # If migrating from a pre-Phase-4 database:
    # op.add_column("forecast_versions", sa.Column("notes", sa.Text, nullable=True))
    pass


def downgrade() -> None:
    # op.drop_column("forecast_versions", "notes")
    pass
