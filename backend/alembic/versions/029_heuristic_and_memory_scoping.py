"""Close two straggler cross-company scoping gaps found during Phase 3.

`learned_heuristics` had no `business_unit_id`/`company_id` at all --
every row today is `scope="line_item"` (the only scope the reflection pass
currently produces), so it's backfilled deterministically from each row's
`line_items.business_unit_id`. Unlike `model_presets`' NULL-means-global
convention, NULL here is a fail-closed *deny* -- a heuristic is never
legitimately visible across companies, so a historical row with no
`line_item_id` (none expected) stays unreachable rather than "global."

`memory_blocks` needed no schema change: its `owner_id` column already
serves as a generic "scope key" (holds a user id for `scope='user'`), but
for `scope='business_unit'` rows the application was writing
`User.business_unit` (the legacy free-text name) into it instead of
`User.business_unit_id` (the real FK) -- see app/services/memory_blocks.py.
This is a data-only backfill: repoint each `business_unit`-scope row's
`owner_id` from the matching `BusinessUnit.name` to that unit's `id`.

Revision ID: 029_heuristic_and_memory_scoping
Revises: 028_company_membership
Create Date: 2026-09-29
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
from migration_helpers import add_column_if_missing, table_exists  # noqa: E402

revision: str = "029_heuristic_and_memory_scoping"
down_revision: Union[str, None] = "028_company_membership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_heuristic_column() -> None:
    add_column_if_missing(
        "learned_heuristics",
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_learned_heuristics_business_unit_id"),
            nullable=True,
        ),
    )


def _backfill_heuristics() -> None:
    if not table_exists("learned_heuristics") or not table_exists("line_items"):
        return
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE learned_heuristics
            SET business_unit_id = (
                SELECT line_items.business_unit_id
                FROM line_items
                WHERE line_items.id = learned_heuristics.line_item_id
            )
            WHERE business_unit_id IS NULL
              AND line_item_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM line_items WHERE line_items.id = learned_heuristics.line_item_id
              )
            """
        )
    )


def _backfill_memory_blocks() -> None:
    if not table_exists("memory_blocks") or not table_exists("business_units"):
        return
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE memory_blocks
            SET owner_id = (
                SELECT business_units.id
                FROM business_units
                WHERE business_units.name = memory_blocks.owner_id
            )
            WHERE scope = 'business_unit'
              AND EXISTS (
                  SELECT 1 FROM business_units WHERE business_units.name = memory_blocks.owner_id
              )
            """
        )
    )


def upgrade() -> None:
    _add_heuristic_column()
    _backfill_heuristics()
    _backfill_memory_blocks()


def downgrade() -> None:
    # Expand-phase migration; downgrade is a no-op (same convention as
    # 025_business_unit.py / 026_scoping_holes_2b.py).
    pass
