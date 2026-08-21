"""Initial schema — superseded by 001_initial (linearized dual-root).

Revision ID: 001
Revises: 001_initial
Create Date: 2026-02-15

Historically this file was a second Alembic root (down_revision=None) that
forked the schema. Phase 0 re-parents it under ``001_initial`` and guts
``upgrade()`` so a fresh install runs a single deterministic chain.
Schema convergence for DBs that already applied the old root lives in
``008_schema_repair``.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty — schema is created by 001_initial.
    # Kept as a revision node so the historical 001 → 002_context_engine
    # branch remains addressable for already-stamped databases.
    pass


def downgrade() -> None:
    pass
