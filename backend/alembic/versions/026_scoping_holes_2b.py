"""Close remaining migration-requiring company-scoping holes (pass 2b).

Adds `business_unit_id` to `model_presets` (nullable -- NULL means "global,
usable by every company", left NULL on backfill so every existing preset
stays usable by everyone exactly as before) and `budget_versions` (nullable,
best-effort backfilled like `025_business_unit.py` backfilled
`actuals_datasets`/`forecast_versions` -- majority vote over the budget's
line items' own business_unit_id, else the "Default" bucket). Swaps
`model_presets`' unique constraint from a bare `name` to `(name,
business_unit_id)` so two companies (or a company and the global catalog)
can each have a preset with the same name.

Also adds `business_unit_settings` (composite PK `business_unit_id, key`)
for per-company overrides of what's currently a single global
`system_settings` row (reporting_currency, fiscal_calendar) -- purely
additive; the existing `system_settings` table and its rows are untouched
and remain the fallback default when no company override exists.

Revision ID: 026_scoping_holes_2b
Revises: 025_business_unit
Create Date: 2026-09-22
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
    create_table_if_missing,
    table_exists,
    unique_constraint_exists,
)

revision: str = "026_scoping_holes_2b"
down_revision: Union[str, None] = "025_business_unit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_BU_NAME = "Default"


def _add_columns() -> None:
    add_column_if_missing(
        "model_presets",
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_model_presets_business_unit_id"),
            nullable=True,
        ),
    )
    add_column_if_missing(
        "budget_versions",
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_budget_versions_business_unit_id"),
            nullable=True,
        ),
    )


def _fix_model_preset_uniqueness() -> None:
    """name was globally unique; make it unique per (name, business_unit_id)."""
    if not table_exists("model_presets"):
        return
    if unique_constraint_exists("model_presets", "uq_model_presets_name_bu"):
        return  # a fresh DB created straight from the current ORM model already has it

    bind = op.get_bind()
    old_name = None
    for uc in sa.inspect(bind).get_unique_constraints("model_presets"):
        if uc["column_names"] == ["name"]:
            old_name = uc["name"] or "uq_model_presets_name"
            break
    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table(
        "model_presets", recreate="always", naming_convention=naming_convention
    ) as batch_op:
        if old_name:
            batch_op.drop_constraint(old_name, type_="unique")
        batch_op.create_unique_constraint(
            "uq_model_presets_name_bu", ["name", "business_unit_id"]
        )


def _backfill_budget_versions() -> None:
    """Best-effort: infer each budget's company from the majority of the
    line items its budget_line_items reference, else Default. Model presets
    are deliberately NOT backfilled -- NULL (global) is the correct value
    for every preset that predates this column."""
    if not table_exists("budget_versions"):
        return
    bind = op.get_bind()

    budget_ids = [
        r[0]
        for r in bind.execute(
            sa.text("SELECT id FROM budget_versions WHERE business_unit_id IS NULL")
        ).fetchall()
    ]
    if not budget_ids:
        return

    default_id = None

    def _get_or_create_default() -> str:
        nonlocal default_id
        if default_id is not None:
            return default_id
        row = bind.execute(
            sa.text("SELECT id FROM business_units WHERE name = :name"),
            {"name": DEFAULT_BU_NAME},
        ).first()
        if row:
            default_id = row[0]
            return default_id
        import uuid
        from datetime import datetime, timezone

        default_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO business_units (id, name, created_at) VALUES (:id, :name, :created_at)"
            ),
            {"id": default_id, "name": DEFAULT_BU_NAME, "created_at": datetime.now(timezone.utc)},
        )
        return default_id

    for budget_id in budget_ids:
        row = bind.execute(
            sa.text(
                "SELECT li.business_unit_id, COUNT(*) AS n "
                "FROM budget_line_items bli "
                "JOIN line_items li ON li.id = bli.line_item_id "
                "WHERE bli.budget_version_id = :budget_id AND li.business_unit_id IS NOT NULL "
                "GROUP BY li.business_unit_id ORDER BY n DESC LIMIT 1"
            ),
            {"budget_id": budget_id},
        ).first()
        bu_id = row[0] if row else _get_or_create_default()
        bind.execute(
            sa.text("UPDATE budget_versions SET business_unit_id = :bu_id WHERE id = :id"),
            {"bu_id": bu_id, "id": budget_id},
        )


def upgrade() -> None:
    _add_columns()
    _fix_model_preset_uniqueness()
    _backfill_budget_versions()
    create_table_if_missing(
        "business_unit_settings",
        sa.Column(
            "business_unit_id",
            sa.String(length=36),
            sa.ForeignKey("business_units.id", name="fk_business_unit_settings_business_unit_id"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    # Expand-phase migration; downgrade is a no-op (same convention as
    # 025_business_unit.py -- nothing downstream reads these columns/table
    # unless this migration has run, so there's nothing to unwind).
    pass
