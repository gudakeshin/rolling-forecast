"""Merge forked Alembic heads (enterprise + context engine).

Revision ID: 004_merge_heads
Revises: 003_enterprise_readiness, 002_context_engine
Create Date: 2026-07-11

Two independent migration chains existed:
  001_initial → 002_add_notes → 003_enterprise_readiness
  001 → 002_context_engine

This merge revision unifies them so `alembic upgrade head` succeeds.
"""

from typing import Sequence, Union

revision: str = "004_merge_heads"
down_revision: Union[str, tuple[str, ...], None] = (
    "003_enterprise_readiness",
    "002_context_engine",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Schema changes already applied on each branch; nothing to do.
    pass


def downgrade() -> None:
    pass
